from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    username: Mapped[str] = mapped_column(String(64))
    username_normalized: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    page_url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "tracking_number", name="uq_orders_user_tracking"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    tracking_number: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    current_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_status_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_event_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(120))
    status_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RouteEvent(Base):
    __tablename__ = "route_events"
    __table_args__ = (
        UniqueConstraint("order_id", "event_key", name="uq_route_events_order_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_text: Mapped[str] = mapped_column(Text)
    courier: Mapped[str | None] = mapped_column(String(160), nullable=True)
    location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
