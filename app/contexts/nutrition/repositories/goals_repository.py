"""repositories/goals_repository.py — дневные цели КБЖУ (NutritionGoals, 1:1/user).

Дельта ТЗ §3: цели уехали с kernel-`User` в Nutrition-сущность по `user_id`
(`Unique`). PERSONAL — читает/пишет только владелец. commit делает сервис.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.nutrition.models import NutritionGoals


class GoalsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int) -> NutritionGoals | None:
        result = await self.session.execute(
            select(NutritionGoals).where(NutritionGoals.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: int,
        kcal: float | None,
        protein: float | None,
        fat: float | None,
        carbs: float | None,
    ) -> NutritionGoals:
        """Создать/обновить строку целей пользователя. None в поле = снять цель."""
        goals = await self.get(user_id)
        if goals is None:
            goals = NutritionGoals(user_id=user_id)
            self.session.add(goals)
        goals.target_kcal = kcal
        goals.target_protein = protein
        goals.target_fat = fat
        goals.target_carbs = carbs
        await self.session.flush()
        return goals
