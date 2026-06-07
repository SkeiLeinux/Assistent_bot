"""
bot.py — точка входа.

Изменение: вместо create_all() теперь запускаем alembic upgrade head.
Это значит что при каждом старте бота все новые миграции применяются
автоматически. Безопасно — если миграций нет, ничего не происходит.
"""

import asyncio
import html
import logging
import traceback
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject, ErrorEvent
from typing import Any, Callable, Awaitable

from config import settings
from db.database import async_session_maker
from handlers import common, lists
from handlers.middleware import AuthMiddleware
from web.api import start_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_migrations():
    import subprocess, sys
    from pathlib import Path

    # Ищем alembic в том же venv что и текущий python.
    # На Windows: .venv/Scripts/alembic.exe
    # На Linux:   .venv/bin/alembic
    python = Path(sys.executable)
    venv_bin = python.parent
    alembic = venv_bin / ("alembic.exe" if sys.platform == "win32" else "alembic")

    # Если alembic не найден рядом с python — падаем с понятной ошибкой
    if not alembic.exists():
        logger.error(f"alembic not found at {alembic}. Run: pip install alembic")
        raise RuntimeError("alembic not found in current venv")

    logger.info("Применяем миграции базы данных...")
    result = subprocess.run(
        [str(alembic), "upgrade", "head"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        logger.error(f"Ошибка миграций:\n{result.stderr}")
        raise RuntimeError("Не удалось применить миграции")
    logger.info("Миграции применены успешно")


class SessionMiddleware(BaseMiddleware):
    """Создаёт сессию БД для каждого входящего апдейта."""
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_maker() as session:
            data["session"] = session
            return await handler(event, data)


async def main():
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    for observer in (dp.message, dp.callback_query):
        observer.middleware(SessionMiddleware())
        observer.middleware(AuthMiddleware())

    dp.include_router(common.router)
    dp.include_router(lists.router)

    # Error handler — ловит все необработанные исключения внутри хендлеров.
    # Срабатывает когда в любом хендлере вылетело исключение которое никто не поймал.
    @dp.error()
    async def error_handler(event: ErrorEvent, bot: Bot) -> None:
        """
        ErrorEvent содержит:
          event.exception — само исключение
          event.update    — апдейт при обработке которого произошла ошибка
        """
        # Всегда логируем в systemd journal
        logger.exception(f"Unhandled exception: {event.exception}", exc_info=event.exception)

        # Если задан admin_telegram_id — отправляем уведомление в Telegram
        if not settings.admin_telegram_id:
            return

        # Формируем читаемый трейсбек
        tb = "".join(traceback.format_exception(
            type(event.exception), event.exception, event.exception.__traceback__
        ))
        # html.escape экранирует < > & чтобы Telegram не сломал HTML-разметку
        tb_escaped = html.escape(tb)

        # Telegram ограничивает сообщения 4096 символами — обрезаем если длиннее
        max_len = 3800
        if len(tb_escaped) > max_len:
            tb_escaped = tb_escaped[-max_len:]  # берём конец — там самое важное
            tb_escaped = "...(обрезано)\n" + tb_escaped

        # Информация об апдейте который вызвал ошибку
        update_info = ""
        if event.update.message:
            u = event.update.message.from_user
            update_info = f"От: {u.full_name} (@{u.username}) | Текст: {event.update.message.text}"
        elif event.update.callback_query:
            u = event.update.callback_query.from_user
            update_info = f"От: {u.full_name} (@{u.username}) | Callback: {event.update.callback_query.data}"

        text = (
            f"🔴 <b>Ошибка в боте</b>\n\n"
            f"<b>Тип:</b> {html.escape(type(event.exception).__name__)}\n"
            f"<b>Сообщение:</b> {html.escape(str(event.exception))}\n"
        )
        if update_info:
            text += f"<b>Контекст:</b> {html.escape(update_info)}\n"
        text += f"\n<pre>{tb_escaped}</pre>"

        try:
            await bot.send_message(
                chat_id=settings.admin_telegram_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            # Если не удалось отправить уведомление — просто логируем
            logger.error(f"Failed to send error notification: {e}")

    run_migrations()

    # Веб-сервер мини-апы "Еда" поднимается в этом же event-loop, что и бот:
    # aiogram ходит в Telegram через polling, aiohttp параллельно отдаёт фронт
    # и JSON-API на той же БД.
    web_runner = await start_web_server()

    # Постоянная кнопка-меню рядом с полем ввода → открывает мини-апу.
    # Ставится только если задан публичный HTTPS-адрес.
    if settings.webapp_url:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🍎 Еда",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            )
            logger.info("Menu Button мини-апы установлен: %s", settings.webapp_url)
        except Exception as e:
            logger.warning("Не удалось установить Menu Button: %s", e)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())