from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FoodEntry, SharedFood


class SharedFoodRepository:
    def __init__(self, session: AsyncSession):
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
    ) -> SharedFood:
        food = SharedFood(
            name=name,
            weight_g=weight_g,
            kcal_per_100g=kcal_per_100g,
            protein_per_100g=protein_per_100g,
            fat_per_100g=fat_per_100g,
            carbs_per_100g=carbs_per_100g,
            created_by=created_by,
        )
        self.session.add(food)
        await self.session.flush()
        return food

    async def search(self, query: str, limit: int = 20) -> list[SharedFood]:
        stmt = (
            select(SharedFood)
            .where(SharedFood.name.ilike(f"%{query}%"))
            .order_by(SharedFood.use_count.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_quick_pick(self, user_id: int, limit: int = 15) -> list[SharedFood]:
        """Недавно использованные этим юзером (по убыванию времени), затем популярные."""
        recent_stmt = (
            select(SharedFood)
            .join(FoodEntry, FoodEntry.shared_food_id == SharedFood.id)
            .where(FoodEntry.user_id == user_id)
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
                .order_by(SharedFood.use_count.desc())
                .limit(limit)
            )
            if recent_ids:
                popular_stmt = popular_stmt.where(~SharedFood.id.in_(list(recent_ids)))
            popular = list((await self.session.execute(popular_stmt)).scalars())
        else:
            popular = []

        return recent + popular

    async def get_by_id(self, food_id: int) -> SharedFood | None:
        return await self.session.get(SharedFood, food_id)

    async def increment_use_count(self, food_id: int) -> None:
        await self.session.execute(
            update(SharedFood)
            .where(SharedFood.id == food_id)
            .values(use_count=SharedFood.use_count + 1)
        )
