from ozon_tracker_bot.__main__ import BOT_COMMANDS
from ozon_tracker_bot.ui import (
    BTN_ADD_ORDER,
    BTN_ARCHIVE,
    BTN_HELP,
    BTN_LIST_ORDERS,
    BTN_REFRESH_ORDERS,
    main_keyboard,
    order_keyboard,
)


def test_main_keyboard_exposes_client_actions() -> None:
    keyboard = main_keyboard()

    labels = [button.text for row in keyboard.keyboard for button in row]

    assert labels == [
        BTN_ADD_ORDER,
        BTN_LIST_ORDERS,
        BTN_REFRESH_ORDERS,
        BTN_ARCHIVE,
        BTN_HELP,
    ]
    assert keyboard.is_persistent is True


def test_order_keyboard_exposes_inline_actions() -> None:
    keyboard = order_keyboard(7)

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert callbacks == [
        "order:status:7",
        "order:history:7",
        "order:refresh:7",
        "order:received:7",
        "order:rename:7",
        "order:archive:7",
    ]


def test_delivered_order_does_not_offer_refresh() -> None:
    keyboard = order_keyboard(7, delivered=True)

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert "order:refresh:7" not in callbacks


def test_telegram_command_menu_is_published() -> None:
    commands = {command.command: command.description for command in BOT_COMMANDS}

    assert commands["start"] == "Открыть меню"
    assert commands["add"] == "Добавить отправление"
    assert commands["cancel"] == "Отменить ввод"
