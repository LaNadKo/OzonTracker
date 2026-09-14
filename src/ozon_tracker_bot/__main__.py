import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .bot import build_router
from .config import Settings
from .database import Database, Repository
from .poller import Poller
from .provider import DEFAULT_OZON_TRACK_PAGE_URL, OzonTrackerProvider
from .service import TrackingService

logger = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand(command="start", description="Открыть меню"),
    BotCommand(command="add", description="Добавить отправление"),
    BotCommand(command="list", description="Мои отправления"),
    BotCommand(command="status", description="Текущий статус заказа"),
    BotCommand(command="history", description="История и маршрут"),
    BotCommand(command="refresh", description="Обновить статусы"),
    BotCommand(command="rename", description="Переименовать заказ"),
    BotCommand(command="remove", description="Архивировать заказ"),
    BotCommand(command="restore", description="Восстановить заказ"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить ввод"),
]


async def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    database = Database(settings.database_url)
    await database.init()

    repository = Repository(database)
    if settings.allowed_usernames:
        await repository.seed_usernames(frozenset(settings.allowed_usernames))
    provider_config = await repository.ensure_provider_config(
        code="ozon_track",
        name="Ozon Track",
        page_url=DEFAULT_OZON_TRACK_PAGE_URL,
    )
    provider = OzonTrackerProvider(
        page_url=provider_config.page_url,
        timeout_seconds=settings.http_timeout_seconds,
    )
    service = TrackingService(repository, provider)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service, settings))

    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.exception("Failed to publish Telegram command menu")

    poller = Poller(service, bot, settings.poll_interval_seconds)
    poller_task = asyncio.create_task(poller.run(), name="status-poller")

    try:
        await dispatcher.start_polling(bot)
    finally:
        poller_task.cancel()
        await asyncio.gather(poller_task, return_exceptions=True)
        await bot.session.close()
        await provider.close()
        await database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
