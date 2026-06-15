"""repositories/dish_ingredient_repository.py — состав составных блюд.

Знает только о базе. Записывает строки состава на родителя (запись дневника ИЛИ
блюдо каталога) и достаёт их батчем по нескольким родителям (без N+1 в дне/каталоге).
commit делает сервис — здесь только flush. Состав иммутабелен (snapshot): отдельного
update нет; правка идёт через пересоздание родителя или удаление каскадом.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.nutrition.models import DishIngredient


class DishIngredientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_for_entry(
        self, food_entry_id: int, ingredients: list[dict]
    ) -> None:
        """Сохранить состав на запись дневника (food_entry_id)."""
        self._add(ingredients, food_entry_id=food_entry_id, shared_food_id=None)
        await self.session.flush()

    async def add_for_food(
        self, shared_food_id: int, ingredients: list[dict]
    ) -> None:
        """Сохранить состав на блюдо каталога (shared_food_id)."""
        self._add(ingredients, food_entry_id=None, shared_food_id=shared_food_id)
        await self.session.flush()

    def _add(
        self,
        ingredients: list[dict],
        *,
        food_entry_id: int | None,
        shared_food_id: int | None,
    ) -> None:
        for pos, ing in enumerate(ingredients):
            self.session.add(
                DishIngredient(
                    food_entry_id=food_entry_id,
                    shared_food_id=shared_food_id,
                    position=pos,
                    name=ing["name"],
                    weight_g=ing.get("weight_g"),
                    kcal=ing.get("kcal") or 0.0,
                    protein=ing.get("protein") or 0.0,
                    fat=ing.get("fat") or 0.0,
                    carbs=ing.get("carbs") or 0.0,
                    source=ing.get("source"),
                    source_ref=ing.get("source_ref"),
                )
            )

    async def for_entries(
        self, entry_ids: list[int]
    ) -> dict[int, list[DishIngredient]]:
        """Состав по нескольким записям дневника: {food_entry_id: [строки по position]}."""
        if not entry_ids:
            return {}
        stmt = (
            select(DishIngredient)
            .where(DishIngredient.food_entry_id.in_(entry_ids))
            .order_by(DishIngredient.food_entry_id, DishIngredient.position)
        )
        out: dict[int, list[DishIngredient]] = {}
        for row in (await self.session.execute(stmt)).scalars():
            out.setdefault(row.food_entry_id, []).append(row)
        return out

    async def for_foods(
        self, food_ids: list[int]
    ) -> dict[int, list[DishIngredient]]:
        """Состав по нескольким блюдам каталога: {shared_food_id: [строки по position]}."""
        if not food_ids:
            return {}
        stmt = (
            select(DishIngredient)
            .where(DishIngredient.shared_food_id.in_(food_ids))
            .order_by(DishIngredient.shared_food_id, DishIngredient.position)
        )
        out: dict[int, list[DishIngredient]] = {}
        for row in (await self.session.execute(stmt)).scalars():
            out.setdefault(row.shared_food_id, []).append(row)
        return out
