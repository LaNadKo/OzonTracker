from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from .formatting import format_change
from .service import TrackingService

logger = logging.getLogger(__name__)


class Poller:
    def __init__(self, service: TrackingService, bot: Bot, interval_seconds: int) -> None:
        self._service = service
        self._bot = bot
        self._interval_seconds = interval_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected polling cycle failure")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self) -> None:
        orders = await self._service.repository.list_pollable_orders()
        for order in orders:
            try:
                result = await self._service.check_pollable(order)
            except Exception as exc:
                logger.warning("Order #%s check failed: %s", order.id, exc)
                continue
            # Every status update notifies the owner, including the very
            # first successful check (previous status is still unknown).
            if not result.status_changed and result.previous_status is not None:
                continue
            try:
                await self._bot.send_message(
                    chat_id=order.user_id,
                    text=format_change(result.order, result.previous_status),
                )
            except Exception:
                logger.exception("Failed to notify user for order #%s", order.id)

