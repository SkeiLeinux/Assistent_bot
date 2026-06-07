"""
handlers/middleware.py — middleware для проверки доступа и автосоздания юзера.

Middleware в aiogram — это слой который выполняется ДО хендлера.
Здесь мы:
  1. Проверяем что пользователь есть в allowed_user_ids
  2. Создаём/достаём пользователя из БД
  3. Кладём его в data['user'] — хендлер получает готовый объект

Это убирает дублирование: без middleware каждый хендлер должен был бы
сам делать эту проверку.
"""

from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from config import settings
from services import UserService


class AuthMiddleware(BaseMiddleware):
    """
    Проверяет доступ и гарантирует наличие пользователя в БД.
    Вешается на весь роутер (или на dispatcher).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Достаём telegram_id из события (Message или CallbackQuery)
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
        else:
            return await handler(event, data)

        if tg_user is None:
            return

        # Проверка whitelist — если список пустой, разрешаем всем
        if settings.allowed_user_ids and tg_user.id not in settings.allowed_user_ids:
            if isinstance(event, Message):
                await event.answer("Извини, у тебя нет доступа к этому боту.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа.", show_alert=True)
            return

        # Получаем сессию из data — aiogram кладёт её туда если настроен
        # SessionMiddleware (см. bot.py)
        session: AsyncSession = data["session"]
        user_service = UserService(session)

        # Автоматически создаём пользователя при первом обращении
        user = await user_service.get_or_create_user(
            telegram_id=tg_user.id,
            full_name=tg_user.full_name,
            username=tg_user.username,
        )

        # Кладём пользователя в data — хендлер получит его как аргумент
        data["user"] = user

        return await handler(event, data)
