from datetime import datetime, timezone

import pytest

from ozon_tracker_bot.database import Database, OrderNotFoundError, Repository
from ozon_tracker_bot.provider import TrackingEvent, TrackingSnapshot
from ozon_tracker_bot.service import TrackingService


class FakeProvider:
    def __init__(self, snapshots: list[TrackingSnapshot]) -> None:
        self.snapshots = iter(snapshots)

    async def get_tracking(self, tracking_number: str) -> TrackingSnapshot:
        return next(self.snapshots)


@pytest.mark.asyncio
async def test_status_history_and_delivered_stop_polling(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    await database.init()
    repository = Repository(database)
    provider_config = await repository.ensure_provider_config(
        "ozon_track", "Ozon Track", "https://tracking.ozon.ru/"
    )
    assert provider_config.code == "ozon_track"
    same_provider_config = await repository.ensure_provider_config(
        "ozon_track", "Changed name", "https://changed.example/"
    )
    assert same_provider_config.page_url == "https://tracking.ozon.ru/"
    provider = FakeProvider(
        [
            TrackingSnapshot(
                "ABC-123",
                "Принято",
                events=(
                    TrackingEvent(
                        "Принято перевозчиком",
                        status="Принято",
                        event_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
                        courier="Ozon Доставка",
                        location="Москва",
                    ),
                ),
            ),
            TrackingSnapshot(
                "ABC-123",
                "В пути",
                events=(
                    TrackingEvent(
                        "Покинул сортировочный центр",
                        status="В пути",
                        event_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
                        courier="Ozon Доставка",
                        location="Казань",
                    ),
                ),
            ),
            TrackingSnapshot(
                "ABC-123",
                "Доставлено",
                delivered=True,
                events=(
                    TrackingEvent(
                        "Получено получателем",
                        status="Доставлено",
                        event_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                        courier="Ozon Доставка",
                        location="Казань",
                    ),
                ),
            ),
        ]
    )
    service = TrackingService(repository, provider)

    order = await service.add_order(42, "ABC-123", "Тестовый заказ")
    first = await service.check_user_order(42, order.id)
    second = await service.check_user_order(42, order.id)
    third = await service.check_user_order(42, order.id)

    assert first.status_changed is False
    assert second.status_changed is True
    assert second.previous_status == "Принято"
    assert third.order.is_delivered is True
    assert third.order.is_active is False
    assert await repository.list_pollable_orders() == []

    history = await repository.get_history(42, order.id)
    assert [item.status for item in history] == ["Доставлено", "В пути", "Принято"]
    route_events = await repository.get_route_events(42, order.id)
    assert [item.location for item in route_events] == ["Москва", "Казань", "Казань"]
    assert route_events[0].courier == "Ozon Доставка"

    await repository.archive_order(42, order.id)
    restored = await repository.restore_order(42, order.id)
    assert restored.is_archived is False
    assert restored.is_active is False  # delivered orders stay terminal after restore

    await database.close()


@pytest.mark.asyncio
async def test_orders_are_isolated_per_user(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'isolated.db').as_posix()}")
    await database.init()
    repository = Repository(database)
    provider = FakeProvider([])
    service = TrackingService(repository, provider)

    own = await service.add_order(42, "ABC-123", "Мой заказ")
    await service.add_order(43, "DEF-456", "Чужой заказ")

    assert [order.id for order in await repository.list_orders(42)] == [own.id]
    assert await repository.list_orders(43) != []

    assert await repository.get_order(43, own.id) is None
    with pytest.raises(OrderNotFoundError):
        await repository.rename_order(43, own.id, "Захват")
    with pytest.raises(OrderNotFoundError):
        await repository.archive_order(43, own.id)

    foreign = await repository.list_orders(43)
    assert foreign[0].tracking_number == "DEF-456"
    assert await repository.get_order(42, foreign[0].id) is None

    await database.close()


@pytest.mark.asyncio
async def test_milestone_completion_updates_route_event_without_duplicates(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'milestones.db').as_posix()}")
    await database.init()
    repository = Repository(database)
    completed_at = datetime(2026, 9, 18, 14, 22, tzinfo=timezone.utc)
    provider = FakeProvider(
        [
            TrackingSnapshot(
                "ABC-123",
                "В пути",
                events=(
                    TrackingEvent(
                        "Его доставят в сортировочный центр",
                        status="Заказ везут в город получателя",
                    ),
                ),
            ),
            TrackingSnapshot(
                "ABC-123",
                "В пути",
                events=(
                    TrackingEvent(
                        "Его доставят в сортировочный центр",
                        status="Заказ везут в город получателя",
                        event_at=completed_at,
                    ),
                ),
            ),
        ]
    )
    service = TrackingService(repository, provider)
    order = await service.add_order(42, "ABC-123", "Тест")

    await service.check_user_order(42, order.id)
    await service.check_user_order(42, order.id)

    route_events = await repository.get_route_events(42, order.id)
    assert len(route_events) == 1
    assert route_events[0].event_at == completed_at.replace(tzinfo=None)

    history = await repository.get_history(42, order.id)
    assert len(history) == 1

    await database.close()


@pytest.mark.asyncio
async def test_legacy_undated_twin_is_cleaned_up(tmp_path) -> None:
    from ozon_tracker_bot.models import RouteEvent

    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'twins.db').as_posix()}")
    await database.init()
    repository = Repository(database)
    completed_at = datetime(2026, 9, 18, 14, 22, tzinfo=timezone.utc)
    provider = FakeProvider(
        [
            TrackingSnapshot(
                "ABC-123",
                "В пути",
                events=(
                    TrackingEvent(
                        "Его доставят в сортировочный центр",
                        status="Заказ везут в город получателя",
                    ),
                ),
            ),
            TrackingSnapshot(
                "ABC-123",
                "В пути",
                events=(
                    TrackingEvent(
                        "Его доставят в сортировочный центр",
                        status="Заказ везут в город получателя",
                        event_at=completed_at,
                    ),
                ),
            ),
        ]
    )
    service = TrackingService(repository, provider)
    order = await service.add_order(42, "ABC-123", "Тест")
    await service.check_user_order(42, order.id)

    # Simulate the legacy bug: a dated twin row inserted alongside the
    # undated one instead of updating it.
    async with database.sessions() as session:
        session.add(
            RouteEvent(
                order_id=order.id,
                event_key="legacy-dated-fingerprint",
                status="Заказ везут в город получателя",
                event_text="Его доставят в сортировочный центр",
                event_at=completed_at,
            )
        )
        await session.commit()

    await service.check_user_order(42, order.id)

    route_events = await repository.get_route_events(42, order.id)
    assert len(route_events) == 1
    assert route_events[0].event_at == completed_at.replace(tzinfo=None)

    await database.close()


@pytest.mark.asyncio
async def test_seeded_usernames_bind_once_to_telegram_id(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'users.db').as_posix()}")
    await database.init()
    repository = Repository(database)

    await repository.seed_usernames(frozenset({"firstuser", "seconduser"}))

    assert await repository.bind_user(1001, "FirstUser") is True
    assert await repository.bind_user(1001, "FirstUser") is True
    assert await repository.bind_user(2002, "FirstUser") is False
    assert await repository.bind_user(2002, "SecondUser") is True
    assert await repository.authorize_user(1001, "FirstUser") is True
    assert await repository.authorize_user(3003, "FirstUser") is False

    await database.close()
