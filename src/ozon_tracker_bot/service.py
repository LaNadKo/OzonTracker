from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .database import OrderNotFoundError, Repository
from .models import Order
from .provider import ProviderError, TrackingSnapshot


@dataclass(frozen=True, slots=True)
class CheckResult:
    order: Order
    snapshot: TrackingSnapshot
    status_changed: bool
    previous_status: str | None


class TrackingProvider(Protocol):
    async def get_tracking(self, tracking_number: str) -> TrackingSnapshot:
        ...


class TrackingService:
    def __init__(self, repository: Repository, provider: TrackingProvider) -> None:
        self.repository = repository
        self.provider = provider

    async def add_order(self, user_id: int, tracking_number: str, title: str) -> Order:
        return await self.repository.add_order(user_id, tracking_number, title)

    async def check_user_order(self, user_id: int, order_id: int) -> CheckResult:
        order = await self.repository.get_order(user_id, order_id)
        if order is None:
            raise OrderNotFoundError("Отправление не найдено.")
        return await self._check_order(order)

    async def check_pollable(self, order: Order) -> CheckResult:
        return await self._check_order(order)

    async def _check_order(self, order: Order) -> CheckResult:
        try:
            snapshot = await self.provider.get_tracking(order.tracking_number)
        except ProviderError as exc:
            await self.repository.record_error(order.id, str(exc))
            raise
        updated, status_changed, previous_status = await self.repository.apply_snapshot(
            order.id, snapshot
        )
        return CheckResult(updated, snapshot, status_changed, previous_status)
