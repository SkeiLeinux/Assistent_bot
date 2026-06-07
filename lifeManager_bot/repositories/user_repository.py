"""
repositories/user_repository.py — слой работы с данными для пользователей.

Репозиторий знает только о базе данных. Он не знает о Telegram,
не знает о бизнес-правилах — просто CRUD (Create, Read, Update, Delete).

Почему отдельный слой, а не писать SQL прямо в хендлерах?
  - Если захочешь поменять базу — меняешь только репозиторий
  - Легко тестировать: можно подменить репозиторий на mock-объект
  - Хендлеры остаются чистыми, в них нет SQL-каши
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        # Сессия приходит снаружи (Dependency Injection).
        # Репозиторий не создаёт сессию сам — это позволяет
        # нескольким репозиториям работать в одной транзакции.
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Найти пользователя по его Telegram ID. Вернёт None если не найден."""
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        """Найти пользователя по внутреннему ID базы данных."""
        return await self.session.get(User, user_id)

    async def create(
        self,
        telegram_id: int,
        full_name: str,
        username: str | None = None,
    ) -> User:
        """Создать нового пользователя."""
        user = User(
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
        )
        self.session.add(user)
        # flush отправляет SQL в базу но НЕ делает commit.
        # После flush у объекта появляется id (база его присвоила),
        # но транзакция ещё открыта — можно откатить.
        await self.session.flush()
        return user

    async def get_family_members(self, family_id: int) -> list[User]:
        """Все участники семьи."""
        result = await self.session.execute(
            select(User).where(User.family_id == family_id)
        )
        return list(result.scalars().all())

    async def update_family(self, user_id: int, family_id: int) -> User | None:
        """Привязать пользователя к семье."""
        user = await self.get_by_id(user_id)
        if user:
            user.family_id = family_id
            await self.session.flush()
        return user

    async def update_food_goals(
        self,
        user_id: int,
        target_kcal: float | None,
        target_protein: float | None,
        target_fat: float | None,
        target_carbs: float | None,
    ) -> User | None:
        """Задать дневные цели КБЖУ. None в поле = снять цель."""
        user = await self.get_by_id(user_id)
        if user:
            user.target_kcal = target_kcal
            user.target_protein = target_protein
            user.target_fat = target_fat
            user.target_carbs = target_carbs
            await self.session.flush()
        return user