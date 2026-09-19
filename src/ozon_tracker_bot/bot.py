from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from .config import Settings
from .database import DuplicateOrderError, OrderNotFoundError
from .formatting import format_order, format_snapshot, format_timeline
from .provider import ProviderError
from .service import TrackingService
from .ui import (
    BTN_ADD_ORDER,
    BTN_ARCHIVE,
    BTN_CANCEL,
    BTN_HELP,
    BTN_LIST_ORDERS,
    BTN_REFRESH_ORDERS,
    add_title_keyboard,
    cancel_keyboard,
    main_keyboard,
    order_keyboard,
)

logger = logging.getLogger(__name__)


class OrderFlow(StatesGroup):
    waiting_tracking = State()
    waiting_title = State()
    waiting_rename = State()


def build_router(service: TrackingService, settings: Settings) -> Router:
    del settings  # Kept in the public signature for compatibility.
    router = Router(name="ozon-tracker")

    async def is_authorized(user: User | None) -> bool:
        return bool(
            user
            and await service.repository.authorize_user(
                telegram_user_id=user.id,
                username=user.username,
            )
        )

    async def deny(message: Message) -> bool:
        if await is_authorized(message.from_user):
            return False
        await message.answer("Доступ запрещён.", reply_markup=main_keyboard())
        logger.warning(
            "Rejected Telegram user %s",
            message.from_user.id if message.from_user else "unknown",
        )
        return True

    async def deny_callback(callback: CallbackQuery) -> bool:
        if await is_authorized(callback.from_user):
            return False
        await callback.answer("Доступ запрещён.", show_alert=True)
        logger.warning("Rejected Telegram callback from user %s", callback.from_user.id)
        return True

    async def send_callback(
        callback: CallbackQuery,
        text: str,
        reply_markup=None,
    ) -> None:
        if callback.message is not None:
            await callback.message.answer(
                text,
                reply_markup=reply_markup or main_keyboard(),
            )
        await callback.answer()

    async def send_order_list(
        message: Message,
        *,
        include_archived: bool = False,
        only_archived: bool = False,
    ) -> None:
        orders = await service.repository.list_orders(
            message.from_user.id,
            include_archived=include_archived,
        )
        if only_archived:
            orders = [order for order in orders if order.is_archived]
        if not orders:
            empty_text = (
                "Архив пуст."
                if only_archived
                else "Список пуст. Добавьте отправление кнопкой «➕ Добавить заказ»."
            )
            await message.answer(empty_text, reply_markup=main_keyboard())
            return
        for order in orders:
            await message.answer(
                format_order(order),
                reply_markup=order_keyboard(
                    order.id,
                    archived=order.is_archived,
                    delivered=order.is_delivered,
                ),
            )

    async def complete_add(
        message: Message,
        state: FSMContext,
        user_id: int,
        tracking_number: str,
        title: str,
    ) -> None:
        await state.clear()
        try:
            order = await service.add_order(user_id, tracking_number, title)
        except DuplicateOrderError as exc:
            await message.answer(str(exc), reply_markup=main_keyboard())
            return

        await message.answer(
            f"Добавлено отправление #{order.id}. Проверяю статус…",
            reply_markup=main_keyboard(),
        )
        try:
            result = await service.check_user_order(user_id, order.id)
        except ProviderError as exc:
            await message.answer(
                f"Заказ сохранён, но проверить его пока не удалось: {escape(str(exc))}",
                reply_markup=main_keyboard(),
            )
            return
        except Exception:
            logger.exception("Initial check failed for order #%s", order.id)
            await message.answer(
                "Заказ сохранён, но проверить его пока не удалось.",
                reply_markup=main_keyboard(),
            )
            return
        await message.answer(
            format_snapshot(result.snapshot),
            reply_markup=order_keyboard(
                order.id,
                delivered=result.order.is_delivered,
            ),
        )

    async def start_add(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(OrderFlow.waiting_tracking)
        await message.answer(
            "Отправьте трек-номер Ozon одним сообщением.\n"
            "Можно сразу дописать название через пробел.",
            reply_markup=cancel_keyboard(),
        )

    async def refresh_all(message: Message) -> None:
        orders = await service.repository.list_orders(message.from_user.id)
        active_orders = [
            order for order in orders if order.is_active and not order.is_delivered
        ]
        if not active_orders:
            await message.answer(
                "Нет активных отправлений для проверки.",
                reply_markup=main_keyboard(),
            )
            return
        for order in active_orders:
            try:
                result = await service.check_user_order(message.from_user.id, order.id)
                text = format_snapshot(result.snapshot)
            except ProviderError as exc:
                text = f"Ошибка: {escape(str(exc))}"
            except Exception:
                logger.exception("Manual refresh failed for order #%s", order.id)
                text = "Внутренняя ошибка проверки."
            await message.answer(
                f"<b>#{order.id} {escape(order.title)}</b>\n{text}",
                reply_markup=main_keyboard(),
            )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await message.answer(
            "Привет! Я храню отправления Ozon и сообщаю об изменении статуса.\n\n"
            "Выберите действие кнопками ниже.",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("help"))
    async def help_command(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await message.answer(_help_text(), reply_markup=main_keyboard())

    @router.message(F.text == BTN_HELP)
    async def help_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await message.answer(_help_text(), reply_markup=main_keyboard())

    @router.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=main_keyboard())

    @router.message(F.text == BTN_CANCEL)
    async def cancel_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=main_keyboard())

    @router.message(F.text == BTN_ADD_ORDER)
    async def add_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await start_add(message, state)

    @router.message(F.text == BTN_LIST_ORDERS)
    async def list_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await send_order_list(message)

    @router.message(F.text == BTN_ARCHIVE)
    async def archive_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await send_order_list(message, include_archived=True, only_archived=True)

    @router.message(F.text == BTN_REFRESH_ORDERS)
    async def refresh_button(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        await refresh_all(message)

    @router.message(Command("add"))
    async def add(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await start_add(message, state)
            return
        tracking_number = parts[1].strip().upper()
        if not _valid_tracking_number(tracking_number):
            await message.answer(
                "Трек-номер должен содержать 4–80 символов: буквы, цифры, точку, "
                "дефис или слэш.",
                reply_markup=main_keyboard(),
            )
            return
        title = (parts[2].strip() if len(parts) == 3 else tracking_number)[:120]
        await complete_add(message, state, message.from_user.id, tracking_number, title)

    @router.message(OrderFlow.waiting_tracking, F.text)
    async def tracking_input(message: Message, state: FSMContext) -> None:
        if await deny(message):
            await state.clear()
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        tracking_number = parts[0].upper() if parts else ""
        if not _valid_tracking_number(tracking_number):
            await message.answer(
                "Не похоже на трек-номер. Отправьте номер ещё раз или нажмите «Отмена».",
                reply_markup=cancel_keyboard(),
            )
            return
        if len(parts) == 2 and parts[1].strip():
            await complete_add(
                message,
                state,
                message.from_user.id,
                tracking_number,
                parts[1].strip()[:120],
            )
            return
        await state.update_data(tracking_number=tracking_number)
        await state.set_state(OrderFlow.waiting_title)
        await message.answer(
            "Трек-номер принят. Введите название заказа или нажмите «Без названия».",
            reply_markup=add_title_keyboard(),
        )

    @router.message(OrderFlow.waiting_title, F.text)
    async def title_input(message: Message, state: FSMContext) -> None:
        if await deny(message):
            await state.clear()
            return
        title = (message.text or "").strip()
        if not title:
            await message.answer(
                "Название не должно быть пустым. Или нажмите «Без названия».",
                reply_markup=add_title_keyboard(),
            )
            return
        data = await state.get_data()
        tracking_number = str(data.get("tracking_number", ""))
        if not tracking_number:
            await state.clear()
            await message.answer(
                "Сессия добавления истекла. Начните заново.",
                reply_markup=main_keyboard(),
            )
            return
        await complete_add(message, state, message.from_user.id, tracking_number, title[:120])

    @router.message(Command("list"))
    async def list_orders(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        include_archived = (message.text or "").lower().strip().endswith(" all")
        await send_order_list(message, include_archived=include_archived)

    @router.message(Command("status"))
    async def status(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        order_id = _single_id(message.text)
        if order_id is None:
            await message.answer(
                "Использование: <code>/status &lt;id&gt;</code>",
                reply_markup=main_keyboard(),
            )
            return
        order = await service.repository.get_order(message.from_user.id, order_id)
        if order is None:
            await message.answer("Отправление не найдено.", reply_markup=main_keyboard())
            return
        await message.answer(
            format_order(order),
            reply_markup=order_keyboard(
                order.id,
                archived=order.is_archived,
                delivered=order.is_delivered,
            ),
        )

    @router.message(Command("history"))
    async def history(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        order_id = _single_id(message.text)
        if order_id is None:
            await message.answer(
                "Использование: <code>/history &lt;id&gt;</code>",
                reply_markup=main_keyboard(),
            )
            return
        order = await service.repository.get_order(message.from_user.id, order_id)
        if order is None:
            await message.answer("Отправление не найдено.", reply_markup=main_keyboard())
            return
        try:
            status_items = await service.repository.get_history(message.from_user.id, order_id)
            route_items = await service.repository.get_route_events(message.from_user.id, order_id)
        except OrderNotFoundError as exc:
            await message.answer(str(exc), reply_markup=main_keyboard())
            return
        await message.answer(
            format_timeline(status_items, route_items),
            reply_markup=order_keyboard(
                order.id,
                archived=order.is_archived,
                delivered=order.is_delivered,
            ),
        )

    @router.message(Command("refresh"))
    async def refresh(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        argument = (message.text or "").split(maxsplit=1)
        if len(argument) == 2 and argument[1].strip().lower() != "all":
            order_id = _parse_id(argument[1])
            if order_id is None:
                await message.answer(
                    "Использование: <code>/refresh [id|all]</code>",
                    reply_markup=main_keyboard(),
                )
                return
            order = await service.repository.get_order(message.from_user.id, order_id)
            if order is None:
                await message.answer("Отправление не найдено.", reply_markup=main_keyboard())
                return
            await _refresh_one(message, service, order.id)
            return
        await refresh_all(message)

    @router.message(Command("rename"))
    async def rename(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3 or _parse_id(parts[1]) is None:
            await message.answer(
                "Использование: <code>/rename &lt;id&gt; &lt;название&gt;</code>",
                reply_markup=main_keyboard(),
            )
            return
        try:
            order = await service.repository.rename_order(
                message.from_user.id, int(parts[1]), parts[2].strip()[:120]
            )
        except OrderNotFoundError as exc:
            await message.answer(str(exc), reply_markup=main_keyboard())
            return
        await message.answer(
            f"Переименовано.\n\n{format_order(order)}",
            reply_markup=order_keyboard(
                order.id,
                archived=order.is_archived,
                delivered=order.is_delivered,
            ),
        )

    @router.message(Command("remove"))
    async def remove(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        order_id = _single_id(message.text)
        if order_id is None:
            await message.answer(
                "Использование: <code>/remove &lt;id&gt;</code>",
                reply_markup=main_keyboard(),
            )
            return
        try:
            order = await service.repository.archive_order(message.from_user.id, order_id)
        except OrderNotFoundError as exc:
            await message.answer(str(exc), reply_markup=main_keyboard())
            return
        await message.answer(
            f"Отправление #{order.id} архивировано. История сохранена.",
            reply_markup=order_keyboard(order.id, archived=True, delivered=order.is_delivered),
        )

    @router.message(Command("restore"))
    async def restore(message: Message, state: FSMContext) -> None:
        if await deny(message):
            return
        await state.clear()
        order_id = _single_id(message.text)
        if order_id is None:
            await message.answer(
                "Использование: <code>/restore &lt;id&gt;</code>",
                reply_markup=main_keyboard(),
            )
            return
        try:
            order = await service.repository.restore_order(message.from_user.id, order_id)
        except OrderNotFoundError as exc:
            await message.answer(str(exc), reply_markup=main_keyboard())
            return
        await message.answer(
            f"Отправление #{order.id} восстановлено.\n\n{format_order(order)}",
            reply_markup=order_keyboard(order.id, delivered=order.is_delivered),
        )

    @router.callback_query(F.data == "flow:cancel")
    async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if await deny_callback(callback):
            return
        await state.clear()
        await send_callback(callback, "Действие отменено.")

    @router.callback_query(F.data == "flow:skip-title")
    async def skip_title_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if await deny_callback(callback):
            return
        if await state.get_state() != OrderFlow.waiting_title.state:
            await callback.answer("Сессия добавления уже завершена.", show_alert=True)
            return
        data = await state.get_data()
        tracking_number = str(data.get("tracking_number", ""))
        if not tracking_number:
            await state.clear()
            await callback.answer("Сессия добавления истекла.", show_alert=True)
            return
        await callback.answer()
        if callback.message is not None:
            await complete_add(
                callback.message,
                state,
                callback.from_user.id,
                tracking_number,
                tracking_number,
            )

    @router.callback_query(F.data.startswith("order:"))
    async def order_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if await deny_callback(callback):
            return
        parts = (callback.data or "").split(":")
        if len(parts) != 3:
            await callback.answer("Некорректное действие.", show_alert=True)
            return
        action = parts[1]
        order_id = _parse_id(parts[2])
        if order_id is None:
            await callback.answer("Некорректный номер отправления.", show_alert=True)
            return

        order = await service.repository.get_order(callback.from_user.id, order_id)
        if order is None:
            await callback.answer("Отправление не найдено.", show_alert=True)
            return

        if action == "status":
            await send_callback(
                callback,
                format_order(order),
                order_keyboard(
                    order.id,
                    archived=order.is_archived,
                    delivered=order.is_delivered,
                ),
            )
            return

        if action == "history":
            status_items = await service.repository.get_history(callback.from_user.id, order_id)
            route_items = await service.repository.get_route_events(callback.from_user.id, order_id)
            await send_callback(
                callback,
                format_timeline(status_items, route_items),
                order_keyboard(
                    order.id,
                    archived=order.is_archived,
                    delivered=order.is_delivered,
                ),
            )
            return

        if action == "refresh":
            try:
                result = await service.check_user_order(callback.from_user.id, order_id)
                text = format_snapshot(result.snapshot)
            except ProviderError as exc:
                text = f"Проверка не удалась: {escape(str(exc))}"
            except Exception:
                logger.exception("Callback refresh failed for order #%s", order_id)
                text = "Проверка не удалась из-за внутренней ошибки."
            await send_callback(
                callback,
                text,
                order_keyboard(
                    order.id,
                    archived=order.is_archived,
                    delivered=order.is_delivered,
                ),
            )
            return

        if action == "received":
            try:
                order = await service.repository.mark_received(
                    callback.from_user.id, order_id
                )
            except OrderNotFoundError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await send_callback(
                callback,
                f"✅ Отмечено как полученное. Заказ перемещён в архив.\n\n{format_order(order)}",
                order_keyboard(order.id, archived=True, delivered=True),
            )
            return

        if action == "archive":
            try:
                order = await service.repository.archive_order(callback.from_user.id, order_id)
            except OrderNotFoundError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await send_callback(
                callback,
                f"Отправление #{order.id} архивировано. История сохранена.",
                order_keyboard(order.id, archived=True, delivered=order.is_delivered),
            )
            return

        if action == "restore":
            try:
                order = await service.repository.restore_order(callback.from_user.id, order_id)
            except OrderNotFoundError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await send_callback(
                callback,
                f"Отправление #{order.id} восстановлено.\n\n{format_order(order)}",
                order_keyboard(order.id, delivered=order.is_delivered),
            )
            return

        if action == "rename":
            await state.clear()
            await state.update_data(rename_order_id=order_id)
            await state.set_state(OrderFlow.waiting_rename)
            await send_callback(
                callback,
                f"Введите новое название для отправления #{order_id}.",
                cancel_keyboard(),
            )
            return

        await callback.answer("Неизвестное действие.", show_alert=True)

    @router.message(OrderFlow.waiting_rename, F.text)
    async def rename_input(message: Message, state: FSMContext) -> None:
        if await deny(message):
            await state.clear()
            return
        title = (message.text or "").strip()
        if not title:
            await message.answer("Название не должно быть пустым.", reply_markup=cancel_keyboard())
            return
        data = await state.get_data()
        order_id = _parse_id(str(data.get("rename_order_id", "")))
        if order_id is None:
            await state.clear()
            await message.answer("Сессия переименования истекла.", reply_markup=main_keyboard())
            return
        try:
            order = await service.repository.rename_order(message.from_user.id, order_id, title[:120])
        except OrderNotFoundError as exc:
            await state.clear()
            await message.answer(str(exc), reply_markup=main_keyboard())
            return
        await state.clear()
        await message.answer(
            f"Переименовано.\n\n{format_order(order)}",
            reply_markup=order_keyboard(
                order.id,
                archived=order.is_archived,
                delivered=order.is_delivered,
            ),
        )

    return router


async def _refresh_one(message: Message, service: TrackingService, order_id: int) -> None:
    try:
        result = await service.check_user_order(message.from_user.id, order_id)
    except ProviderError as exc:
        await message.answer(
            f"Проверка не удалась: {escape(str(exc))}",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Manual refresh failed for order #%s", order_id)
        await message.answer(
            "Проверка не удалась из-за внутренней ошибки.",
            reply_markup=main_keyboard(),
        )
        return
    await message.answer(
        format_snapshot(result.snapshot),
        reply_markup=order_keyboard(
            result.order.id,
            archived=result.order.is_archived,
            delivered=result.order.is_delivered,
        ),
    )


def _help_text() -> str:
    return (
        "<b>Управление кнопками</b>\n"
        "«➕ Добавить заказ» — пошаговое добавление трека\n"
        "«📦 Мои заказы» — карточки отправлений\n"
        "«🔄 Обновить статусы» — проверить все активные\n"
        "«🗃 Архив» — архивные отправления\n\n"
        "Дополнительно доступны команды:\n"
        "/add &lt;трек&gt; [название]\n"
        "/list [all], /status &lt;id&gt;, /history &lt;id&gt;\n"
        "/refresh [id|all], /rename &lt;id&gt; &lt;название&gt;\n"
        "/remove &lt;id&gt;, /restore &lt;id&gt;, /cancel"
    )


def _single_id(text: str | None) -> int | None:
    parts = (text or "").split()
    return _parse_id(parts[1]) if len(parts) == 2 else None


def _parse_id(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None


def _valid_tracking_number(value: str) -> bool:
    if not 4 <= len(value) <= 80:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    return all(char in allowed for char in value)
