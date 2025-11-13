import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from database.core import add_admin, ensure_database
from handlers import admin as admin_handlers
from handlers import user as user_handlers
from utils.config import get_settings
from utils.logger import setup_logging
from utils.system import mark_bot_started
from utils.profile import periodic_profile_updates


def register_routers(dispatcher: Dispatcher) -> None:
    """Attach project routers to dispatcher."""
    dispatcher.include_routers(
        admin_handlers.admin_router,
        user_handlers.user_router,
    )


async def main() -> None:
    """Entrypoint for running the Telegram bot."""
    setup_logging()
    ensure_database()
    mark_bot_started()

    settings = get_settings()
    add_admin(settings.main_admin_id, username=None)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)
    register_routers(dispatcher)

    logging.info("Bot ishga tushmoqda...")
    profile_task = asyncio.create_task(periodic_profile_updates(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        profile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await profile_task


if __name__ == "__main__":
    asyncio.run(main())
