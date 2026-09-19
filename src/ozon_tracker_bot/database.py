from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, Order, ProviderConfig, RouteEvent, StatusHistory, User, utc_now
from .provider import TrackingEvent, TrackingSnapshot


class DuplicateOrderError(ValueError):
    pass


class OrderNotFoundError(ValueError):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self._ensure_sqlite_directory(url)
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _ensure_sqlite_directory(url: str) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return
        raw_path = url[len(prefix) :]
        if raw_path == ":memory:":
            return
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


class Repository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_order(self, user_id: int, tracking_number: str, title: str) -> Order:
        async with self._database.sessions() as session:
            order = Order(user_id=user_id, tracking_number=tracking_number, title=title)
            session.add(order)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateOrderError("Такой трек уже добавлен.") from exc
            await session.refresh(order)
            return order

    async def seed_usernames(self, usernames: set[str] | frozenset[str]) -> None:
        async with self._database.sessions() as session:
            for username in usernames:
                normalized = username.strip().lstrip("@").lower()
                if not normalized:
                    continue
                existing = (
                    await session.execute(
                        select(User).where(User.username_normalized == normalized)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        User(
                            username=f"@{normalized}",
                            username_normalized=normalized,
                        )
                    )
                else:
                    existing.username = f"@{normalized}"
                    existing.is_active = True
                    existing.updated_at = utc_now()
            await session.commit()

    async def ensure_provider_config(
        self, code: str, name: str, page_url: str
    ) -> ProviderConfig:
        async with self._database.sessions() as session:
            provider = (
                await session.execute(
                    select(ProviderConfig).where(ProviderConfig.code == code)
                )
            ).scalar_one_or_none()
            if provider is None:
                provider = ProviderConfig(code=code, name=name, page_url=page_url)
                session.add(provider)
                await session.commit()
                await session.refresh(provider)
                return provider

            if not provider.page_url:
                provider.page_url = page_url
                provider.updated_at = utc_now()
                await session.commit()
            return provider

    async def bind_user(self, telegram_user_id: int, username: str | None) -> bool:
        """Bind a seeded username to its first observed Telegram ID.

        If a username was already bound to another ID, fail closed.
        """
        if not username:
            return False
        normalized = username.strip().lstrip("@").lower()
        async with self._database.sessions() as session:
            user = (
                await session.execute(
                    select(User).where(User.username_normalized == normalized)
                )
            ).scalar_one_or_none()
            if user is None:
                return False
            if user.telegram_user_id not in (None, telegram_user_id):
                return False
            user.telegram_user_id = telegram_user_id
            user.last_seen_at = utc_now()
            user.updated_at = utc_now()
            await session.commit()
            return True

    async def authorize_user(
        self,
        telegram_user_id: int,
        username: str | None,
    ) -> bool:
        normalized = username.strip().lstrip("@").lower() if username else ""
        async with self._database.sessions() as session:
            by_id = (
                await session.execute(
                    select(User).where(
                        User.telegram_user_id == telegram_user_id,
                        User.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if by_id is not None:
                by_id.last_seen_at = utc_now()
                by_id.updated_at = utc_now()
                await session.commit()
                return True

            by_username = None
            if normalized:
                by_username = (
                    await session.execute(
                        select(User).where(
                            User.username_normalized == normalized,
                            User.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
            if by_username is not None:
                if by_username.telegram_user_id not in (None, telegram_user_id):
                    return False
                by_username.telegram_user_id = telegram_user_id
                by_username.last_seen_at = utc_now()
                by_username.updated_at = utc_now()
                await session.commit()
                return True

        return False

    async def get_order(self, user_id: int, order_id: int) -> Order | None:
        async with self._database.sessions() as session:
            statement = select(Order).where(Order.id == order_id, Order.user_id == user_id)
            return (await session.execute(statement)).scalar_one_or_none()

    async def list_orders(self, user_id: int, include_archived: bool = False) -> list[Order]:
        async with self._database.sessions() as session:
            statement = select(Order).where(Order.user_id == user_id)
            if not include_archived:
                statement = statement.where(Order.is_archived.is_(False))
            statement = statement.order_by(Order.is_archived, desc(Order.created_at), Order.id)
            return list((await session.execute(statement)).scalars().all())

    async def list_pollable_orders(self) -> list[Order]:
        async with self._database.sessions() as session:
            statement = (
                select(Order)
                .where(
                    Order.is_active.is_(True),
                    Order.is_archived.is_(False),
                    Order.is_delivered.is_(False),
                )
                .order_by(Order.id)
            )
            return list((await session.execute(statement)).scalars().all())

    async def get_history(self, user_id: int, order_id: int, limit: int = 20) -> list[StatusHistory]:
        async with self._database.sessions() as session:
            order_exists = (
                await session.execute(
                    select(Order.id).where(Order.id == order_id, Order.user_id == user_id)
                )
            ).scalar_one_or_none()
            if order_exists is None:
                raise OrderNotFoundError("Отправление не найдено.")
            statement = (
                select(StatusHistory)
                .where(StatusHistory.order_id == order_id)
                .order_by(desc(StatusHistory.event_at), desc(StatusHistory.received_at))
                .limit(limit)
            )
            return list((await session.execute(statement)).scalars().all())

    async def get_route_events(self, user_id: int, order_id: int, limit: int = 50) -> list[RouteEvent]:
        async with self._database.sessions() as session:
            order_exists = (
                await session.execute(
                    select(Order.id).where(Order.id == order_id, Order.user_id == user_id)
                )
            ).scalar_one_or_none()
            if order_exists is None:
                raise OrderNotFoundError("Отправление не найдено.")
            statement = (
                select(RouteEvent)
                .where(RouteEvent.order_id == order_id)
                # Rows are replaced wholesale on every check in the page's
                # sequence order, so insertion id mirrors the route order.
                .order_by(RouteEvent.id.asc())
                .limit(limit)
            )
            return list((await session.execute(statement)).scalars().all())

    async def rename_order(self, user_id: int, order_id: int, title: str) -> Order:
        async with self._database.sessions() as session:
            order = await self._get_order_in_session(session, user_id, order_id)
            order.title = title
            order.updated_at = utc_now()
            await session.commit()
            return order

    async def archive_order(self, user_id: int, order_id: int) -> Order:
        async with self._database.sessions() as session:
            order = await self._get_order_in_session(session, user_id, order_id)
            order.is_archived = True
            order.is_active = False
            order.updated_at = utc_now()
            await session.commit()
            return order

    async def restore_order(self, user_id: int, order_id: int) -> Order:
        async with self._database.sessions() as session:
            order = await self._get_order_in_session(session, user_id, order_id)
            order.is_archived = False
            order.is_active = not order.is_delivered
            order.updated_at = utc_now()
            await session.commit()
            return order

    async def apply_snapshot(
        self,
        order_id: int,
        snapshot: TrackingSnapshot,
    ) -> tuple[Order, bool, str | None, bool]:
        async with self._database.sessions() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise OrderNotFoundError("Отправление не найдено.")

            previous_status = order.current_status
            status_changed = previous_status is not None and previous_status != snapshot.status
            event_changed = (
                snapshot.latest_event is not None
                and order.last_event_text != snapshot.latest_event.full_text
            )

            order.current_status = snapshot.status
            order.current_status_code = snapshot.status_code
            order.is_delivered = snapshot.delivered
            order.is_active = not snapshot.delivered
            if snapshot.delivered:
                # Delivered orders move to the archive and stop polling.
                order.is_archived = True
            order.last_checked_at = utc_now()
            order.last_error = None
            order.error_count = 0
            order.tracking_url = snapshot.tracking_url or order.tracking_url
            if snapshot.latest_event is not None:
                order.last_event_text = snapshot.latest_event.full_text
                order.last_event_at = snapshot.latest_event.event_at
            order.updated_at = utc_now()

            if snapshot.events:
                # The Ozon page always lists the whole route in sequence
                # order; replace the stored route wholesale so row order
                # matches the page and completed steps never linger as
                # planned twins.
                await session.execute(
                    delete(RouteEvent).where(RouteEvent.order_id == order.id)
                )
                for event in snapshot.events:
                    session.add(_route_event_from_tracking_event(order.id, event))

            if previous_status is None or status_changed:
                event = snapshot.latest_event
                session.add(
                    StatusHistory(
                        order_id=order.id,
                        status=snapshot.status,
                        status_code=snapshot.status_code,
                        event_text=event.full_text if event else None,
                        event_at=event.event_at if event else None,
                    )
                )
            await session.commit()
            return order, status_changed, previous_status, event_changed

    async def mark_received(self, user_id: int, order_id: int) -> Order:
        """Manual confirmation: the page may never register the handout."""
        async with self._database.sessions() as session:
            order = await self._get_order_in_session(session, user_id, order_id)
            order.is_delivered = True
            order.is_active = False
            order.is_archived = True
            order.current_status = "Заказ получен в пункте выдачи"
            order.current_status_code = "delivered"
            order.last_error = None
            order.error_count = 0
            order.last_checked_at = utc_now()
            order.updated_at = utc_now()
            session.add(
                StatusHistory(
                    order_id=order.id,
                    status="Заказ получен в пункте выдачи",
                    status_code="delivered",
                    event_text="Подтверждено пользователем",
                )
            )
            await session.commit()
            return order

    async def record_error(self, order_id: int, message: str) -> None:
        async with self._database.sessions() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return
            order.last_error = message[:500]
            order.error_count += 1
            order.last_checked_at = utc_now()
            order.updated_at = utc_now()
            await session.commit()

    async def _get_order_in_session(
        self, session: AsyncSession, user_id: int, order_id: int
    ) -> Order:
        statement = select(Order).where(Order.id == order_id, Order.user_id == user_id)
        order = (await session.execute(statement)).scalar_one_or_none()
        if order is None:
            raise OrderNotFoundError("Отправление не найдено.")
        return order


def _route_event_from_tracking_event(order_id: int, event: TrackingEvent) -> RouteEvent:
    return RouteEvent(
        order_id=order_id,
        event_key=event.fingerprint,
        status=event.status,
        status_code=event.status_code,
        event_text=event.text,
        courier=event.courier,
        location=event.location,
        event_at=event.event_at,
    )
