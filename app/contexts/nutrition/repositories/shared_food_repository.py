"""repositories/shared_food_repository.py — каталог блюд (SharedFood, FAMILY).

Видимость FAMILY (ТЗ §6 I4, дельта OQ-N2): каталог общий внутри семьи. Все
выборки фильтруются по `family_id == viewer.family_id` (чтения только в рамках
своей семьи). `create` принимает `family_id`. commit делает сервис.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.nutrition.models import FoodEntry, SharedFood


class SharedFoodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        name: str,
        weight_g: float,
        kcal_per_100g: float,
        protein_per_100g: float,
        fat_per_100g: float,
        carbs_per_100g: float,
        created_by: int,
        family_id: int,
    ) -> SharedFood:
        food = SharedFood(
            name=name,
            weight_g=weight_g,
            kcal_per_100g=kcal_per_100g,
            protein_per_100g=protein_per_100g,
            fat_per_100g=fat_per_100g,
            carbs_per_100g=carbs_per_100g,
            created_by=created_by,
            family_id=family_id,
        )
        self.session.add(food)
        await self.session.flush()
        return food

    async def search(
        self, family_id: int, query: str, limit: int = 20
    ) -> list[SharedFood]:
        """Каталог семьи по подстроке названия, по убыванию use_count (quick-pick)."""
        stmt = (
            select(SharedFood)
            .where(
                SharedFood.family_id == family_id,
                SharedFood.name.ilike(f"%{query}%"),
            )
            .order_by(SharedFood.use_count.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_quick_pick(
        self, family_id: int, user_id: int, limit: int = 15
    ) -> list[SharedFood]:
        """Недавно использованные этим юзером (по времени), затем популярные в семье."""
        recent_stmt = (
            select(SharedFood)
            .join(FoodEntry, FoodEntry.shared_food_id == SharedFood.id)
            .where(
                SharedFood.family_id == family_id,
                FoodEntry.user_id == user_id,
            )
            .group_by(SharedFood.id)
            .order_by(func.max(FoodEntry.created_at).desc())
            .limit(limit)
        )
        recent = list((await self.session.execute(recent_stmt)).scalars())
        recent_ids = {f.id for f in recent}

        remaining = limit - len(recent)
        if remaining > 0:
            popular_stmt = (
                select(SharedFood)
                .where(SharedFood.family_id == family_id)
                .order_by(SharedFood.use_count.desc())
                .limit(limit)
            )
            if recent_ids:
                popular_stmt = popular_stmt.where(
                    ~SharedFood.id.in_(list(recent_ids))
                )
            popular = list((await self.session.execute(popular_stmt)).scalars())
        else:
            popular = []

        return recent + popular

    async def get_by_id(self, food_id: int) -> SharedFood | None:
        return await self.session.get(SharedFood, food_id)

    async def get_visible(self, family_id: int, food_id: int) -> SharedFood | None:
        """Блюдо каталога, видимое семье зрителя (FAMILY-фильтр)."""
        stmt = select(SharedFood).where(
            SharedFood.id == food_id, SharedFood.family_id == family_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def increment_use_count(self, food_id: int) -> None:
        await self.session.execute(
            update(SharedFood)
            .where(SharedFood.id == food_id)
            .values(use_count=SharedFood.use_count + 1)
        )
