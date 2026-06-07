"""
repositories/food_repository.py — CRUD для дневника питания (FoodEntry).

Как и остальные репозитории: знает только о базе, ничего о Telegram и
бизнес-правилах. commit делает сервис — здесь только flush.

Про время:
  created_at хранится как наивный UTC (SQLite CURRENT_TIMESTAMP = UTC).
  Поэтому выборки за день/период принимают ГОТОВЫЕ границы [start, end) в UTC.
  Перевод "сегодня в таймзоне пользователя" → UTC-границы делает сервис
  (FoodService), чтобы репозиторий оставался тупым диапазонным запросом.
"""

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import FoodEntry


class FoodRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        description: str,
        kcal: float,
        protein: float,
        fat: float,
        carbs: float,
        weight_g: float | None = None,
        meal_type: str | None = None,
        source: str = "manual",
        photo_file_id: str | None = None,
        created_at: datetime | None = None,
        shared_food_id: int | None = None,
    ) -> FoodEntry:
        entry = FoodEntry(
            user_id=user_id,
            description=description,
            kcal=kcal,
            protein=protein,
            fat=fat,
            carbs=carbs,
            weight_g=weight_g,
            meal_type=meal_type,
            source=source,
            photo_file_id=photo_file_id,
            shared_food_id=shared_food_id,
        )
        # created_at задаём только если передали явно (бэкдейтинг).
        # Иначе сработает server_default=func.now() из модели.
        if created_at is not None:
            entry.created_at = created_at
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_by_id(self, entry_id: int) -> FoodEntry | None:
        return await self.session.get(FoodEntry, entry_id)

    async def get_in_range(
        self, user_id: int, start: datetime, end: datetime
    ) -> list[FoodEntry]:
        """
        Все записи пользователя в полуинтервале [start, end) по UTC,
        отсортированные по времени приёма пищи.
        Используется и для "за день", и для "за период".
        """
        result = await self.session.execute(
            select(FoodEntry)
            .where(
                FoodEntry.user_id == user_id,
                FoodEntry.created_at >= start,
                FoodEntry.created_at < end,
            )
            .order_by(FoodEntry.created_at)
        )
        return list(result.scalars().all())

    async def update(self, entry_id: int, **fields) -> FoodEntry | None:
        """Обновить разрешённые поля записи. Неизвестные поля игнорируются."""
        entry = await self.get_by_id(entry_id)
        if not entry:
            return None
        allowed = {
            "description", "kcal", "protein", "fat", "carbs",
            "weight_g", "meal_type", "created_at",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(entry, key, value)
        await self.session.flush()
        return entry

    async def delete(self, entry_id: int) -> bool:
        entry = await self.get_by_id(entry_id)
        if not entry:
            return False
        await self.session.delete(entry)
        await self.session.flush()
        return True
