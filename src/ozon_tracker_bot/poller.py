from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from .formatting import format_change, format_event_update
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
            # Every status update notifies the owner: the first successful
            # check (previous status is still unknown), a coarse status change,
            # and a new precise route milestone within the same coarse status.
            if result.status_changed or result.previous_status is None:
                text = format_change(result.order, result.previous_status)
            elif result.event_changed:
                text = format_event_update(result.order)
            else:
                continue
            try:
                await self._bot.send_message(
                    chat_id=order.user_id,
                    text=text,
                )
            except Exception:
                logger.exception("Failed to notify user for order #%s", order.id)

