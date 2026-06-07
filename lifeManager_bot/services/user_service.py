"""
services/user_service.py — бизнес-логика для пользователей и семей.

Сервис — это место где живут правила. Например:
  "Нельзя создать вторую семью если уже состоишь в одной"
  "При вступлении в семью по коду — обновить family_id"

Сервис работает через репозитории и не знает о Telegram.
На входе — простые типы (int, str). На выходе — объекты моделей или
простые dataclass-и с результатом операции.
"""

from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User, Family
from repositories import UserRepository, FamilyRepository


@dataclass
class UserServiceResult:
    """Результат операции сервиса — объект + необязательное сообщение."""
    success: bool
    message: str = ""
    user: User | None = None
    family: Family | None = None


class UserService:
    def __init__(self, session: AsyncSession):
        # Оба репозитория работают с одной и той же сессией.
        # Это значит что их операции находятся в одной транзакции —
        # либо всё сохранится, либо ничего (атомарность).
        self.user_repo = UserRepository(session)
        self.family_repo = FamilyRepository(session)
        self.session = session

    async def get_or_create_user(
        self,
        telegram_id: int,
        full_name: str,
        username: str | None = None,
    ) -> User:
        """
        Найти существующего пользователя или создать нового.
        Вызывается при каждом /start и при первом сообщении.
        """
        user = await self.user_repo.get_by_telegram_id(telegram_id)
        if not user:
            user = await self.user_repo.create(
                telegram_id=telegram_id,
                full_name=full_name,
                username=username,
            )
            await self.session.commit()
        return user

    async def create_family(
        self, user: User, family_name: str
    ) -> UserServiceResult:
        """
        Создать новую семью и сразу добавить туда пользователя.
        Нельзя создать семью если уже состоишь в одной.
        """
        if user.family_id is not None:
            return UserServiceResult(
                success=False,
                message="Ты уже состоишь в группе. Сначала выйди из неё.",
            )

        family = await self.family_repo.create(name=family_name)
        await self.user_repo.update_family(user.id, family.id)
        await self.session.commit()

        return UserServiceResult(
            success=True,
            message=f"Группа «{family.name}» создана!\nИнвайт-код: `{family.invite_code}`",
            family=family,
            user=user,
        )

    async def join_family(
        self, user: User, invite_code: str
    ) -> UserServiceResult:
        """Вступить в существующую семью по инвайт-коду."""
        if user.family_id is not None:
            return UserServiceResult(
                success=False,
                message="Ты уже состоишь в группе.",
            )

        family = await self.family_repo.get_by_invite_code(invite_code)
        if not family:
            return UserServiceResult(
                success=False,
                message="Группа с таким кодом не найдена. Проверь код и попробуй снова.",
            )

        await self.user_repo.update_family(user.id, family.id)
        await self.session.commit()

        return UserServiceResult(
            success=True,
            message=f"Ты вступил в группу «{family.name}»!",
            family=family,
        )

    async def get_user_with_family(self, telegram_id: int) -> User | None:
        """Получить пользователя. Нужен для большинства операций."""
        return await self.user_repo.get_by_telegram_id(telegram_id)
