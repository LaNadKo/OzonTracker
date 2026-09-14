from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_ADD_ORDER = "➕ Добавить заказ"
BTN_LIST_ORDERS = "📦 Мои заказы"
BTN_REFRESH_ORDERS = "🔄 Обновить статусы"
BTN_ARCHIVE = "🗃 Архив"
BTN_HELP = "❓ Помощь"
BTN_CANCEL = "Отмена"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_ADD_ORDER),
                KeyboardButton(text=BTN_LIST_ORDERS),
            ],
            [
                KeyboardButton(text=BTN_REFRESH_ORDERS),
                KeyboardButton(text=BTN_ARCHIVE),
            ],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="flow:cancel")]
        ]
    )


def add_title_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без названия", callback_data="flow:skip-title"
                )
            ],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="flow:cancel")],
        ]
    )


def order_keyboard(
    order_id: int,
    *,
    archived: bool = False,
    delivered: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📌 Статус", callback_data=f"order:status:{order_id}"
            ),
            InlineKeyboardButton(
                text="📜 История", callback_data=f"order:history:{order_id}"
            ),
        ]
    ]
    if not archived and not delivered:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Проверить", callback_data=f"order:refresh:{order_id}"
                )
            ]
        )
    if archived:
        rows.append(
            [
                InlineKeyboardButton(
                    text="♻️ Восстановить", callback_data=f"order:restore:{order_id}"
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📝 Переименовать", callback_data=f"order:rename:{order_id}"
                ),
                InlineKeyboardButton(
                    text="🗄 Архивировать", callback_data=f"order:archive:{order_id}"
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
