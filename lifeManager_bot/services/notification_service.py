"""
services/notification_service.py — отправка уведомлений участникам семьи.

Принцип: когда один участник что-то меняет в списке, остальные получают
короткое сообщение об этом. Это и есть главная ценность совместного
менеджера — ты всегда знаешь что изменил партнёр.

Почему сервис получает Bot как параметр, а не импортирует его:
  Сервисный слой не должен знать про Telegram. Bot передаётся снаружи
  (из хендлера) — это Dependency Injection. Так сервис легко тестировать
  и он остаётся независимым от транспортного слоя.

Почему не вызывать из ListService:
  ListService тоже не должен знать про Bot. Уведомления — это side effect
  который живёт на уровне хендлеров: "операция прошла → оповести остальных".
"""

import logging
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, ListCategory, ListItem
from repositories import UserRepository

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot, session: AsyncSession):
        self.bot = bot
        self.session = session
        self.user_repo = UserRepository(session)

    async def _get_recipients(self, actor: User) -> list[User]:
        """
        Возвращает всех участников семьи кроме того кто сделал действие.
        Если семьи нет или пользователь один — пустой список.
        """
        if not actor.family_id:
            return []
        members = await self.user_repo.get_family_members(actor.family_id)
        return [m for m in members if m.telegram_id != actor.telegram_id]

    async def _send(self, recipients: list[User], text: str) -> None:
        """
        Отправляет сообщение каждому получателю.
        Ошибки не прерывают основную операцию — если не удалось отправить
        уведомление (пользователь заблокировал бота и т.п.), просто логируем.
        """
        for user in recipients:
            try:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
            except TelegramForbiddenError:
                # Пользователь заблокировал бота — не критично
                logger.warning(f"Cannot send notification to {user.telegram_id}: bot blocked")
            except TelegramBadRequest as e:
                logger.warning(f"Cannot send notification to {user.telegram_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending notification to {user.telegram_id}: {e}")

    def _cat_title(self, category: ListCategory) -> str:
        """Форматирует название категории с эмодзи."""
        return f"{category.emoji} {category.name}" if category.emoji else category.name

    # ── Публичные методы — по одному на тип события ────────────────────────

    async def item_added(
        self, actor: User, item: ListItem, category: ListCategory
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> добавил(а) "
            f"<i>{item.text}</i> в {self._cat_title(category)}",
        )

    async def item_checked(
        self, actor: User, item: ListItem, category: ListCategory
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        icon = "✅" if item.is_checked else "☐"
        action = "отметил(а)" if item.is_checked else "снял(а) отметку с"
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> {action} "
            f"{icon} <i>{item.text}</i> в {self._cat_title(category)}",
        )

    async def item_edited(
        self, actor: User, item: ListItem, category: ListCategory, old_text: str
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> изменил(а) пункт в {self._cat_title(category)}\n"
            f"<s>{old_text}</s> → <i>{item.text}</i>",
        )

    async def item_deleted(
        self, actor: User, item_text: str, category: ListCategory
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> удалил(а) "
            f"<i>{item_text}</i> из {self._cat_title(category)}",
        )

    async def category_created(
        self, actor: User, category: ListCategory
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> создал(а) новый раздел "
            f"{self._cat_title(category)}",
        )

    async def category_deleted(
        self, actor: User, category_title: str
    ) -> None:
        recipients = await self._get_recipients(actor)
        if not recipients:
            return
        await self._send(
            recipients,
            f"👤 <b>{actor.full_name}</b> удалил(а) раздел "
            f"<i>{category_title}</i>",
        )