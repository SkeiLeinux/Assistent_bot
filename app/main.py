"""app.main — точка входа: композиция в одном event-loop.

Поднимает по порядку: миграции → Bot+Dispatcher → web (мини-апа) → scheduler → polling.
Запуск только из корня репозитория (pydantic-settings и alembic берут пути от CWD):

    python -m app.main
"""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.delivery.bot import build_dispatcher
from app.delivery.miniapp_api import start_web_server
from app.scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """alembic upgrade head из того же venv, что и текущий python.

    На Windows alembic.exe лежит в .venv/Scripts рядом с python; на Linux — в .venv/bin.
    """
    python = Path(sys.executable)
    alembic = python.parent / ("alembic.exe" if sys.platform == "win32" else "alembic")
    if not alembic.exists():
        logger.error("alembic не найден рядом с python: %s. Запусти: pip install alembic", alembic)
        raise RuntimeError("alembic not found in current venv")

    logger.info("Применяем миграции базы данных...")
    result = subprocess.run(
        [str(alembic), "upgrade", "head"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        logger.error("Ошибка миграций:\n%s", result.stderr)
        raise RuntimeError("Не удалось применить миграции")
    logger.info("Миграции применены успешно")


async def main() -> None:
    run_migrations()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    # Веб-сервер мини-апы и планировщик — в том же event-loop, что и бот:
    # aiogram ходит в Telegram через polling, aiohttp параллельно отдаёт фронт+API,
    # scheduler тикает по таймеру. Общая БД — одна async_session_maker.
    web_runner = await start_web_server()
    scheduler = Scheduler(interval_seconds=60.0)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await scheduler.stop()
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
