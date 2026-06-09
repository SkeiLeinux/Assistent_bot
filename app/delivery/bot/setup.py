"""Сборка Telegram-входа: Dispatcher (сессия + whitelist/provision + пейринг).

Композиция (Bot, polling, web, scheduler) — в app/main.py.
Срез 1: Lists/Food сняты с проводки; включён только kernel-пейринг; menu-button
убран (фронта Mini App под kernel пока нет — вернётся в S4).
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject

from app import notifications
from app.config import settings
from app.db.engine import async_session_maker
from app.delivery.bot.handlers import router as pairing_router
from app.kernel.services import IdentityService

logger = logging.getLogger(__name__)


class SessionMiddleware(BaseMiddleware):
    """Создаёт сессию БД на каждый входящий апдейт."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_maker() as session:
            data["session"] = session
            return await handler(event, data)


class WhitelistProvisionMiddleware(BaseMiddleware):
    """Whitelist (ALLOWED_USER_IDS) + auto-provision kernel-User в data['user']."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)

        if settings.allowed_user_ids and tg_user.id not in settings.allowed_user_ids:
            if isinstance(event, Message):
                await event.answer("Извини, у тебя нет доступа к этому боту.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа.", show_alert=True)
            return None

        session = data["session"]
        user = await IdentityService(session).get_or_create_user(
            telegram_user_id=tg_user.id,
            first_name=tg_user.first_name,
            username=tg_user.username,
        )
        await session.commit()
        data["user"] = user
        return await handler(event, data)


def _error_context(event: ErrorEvent) -> str:
    upd = event.update
    if upd.message:
        u = upd.message.from_user
        return f"От: {u.full_name} (@{u.username}) | Текст: {upd.message.text}"
    if upd.callback_query:
        u = upd.callback_query.from_user
        return f"От: {u.full_name} (@{u.username}) | Callback: {upd.callback_query.data}"
    return ""


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    for observer in (dp.message, dp.callback_query):
        observer.middleware(SessionMiddleware())
        observer.middleware(WhitelistProvisionMiddleware())

    dp.include_router(pairing_router)

    @dp.error()
    async def error_handler(event: ErrorEvent, bot: Bot) -> None:
        await notifications.alert_admin(bot, event.exception, _error_context(event))

    return dp
