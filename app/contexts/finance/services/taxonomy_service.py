"""TaxonomyService — категории, очередь ревью, resolve (accept/alias) (ТЗ §8, S6).

NEEDS_REVIEW → ACTIVE двумя путями (§8):
  - accept: подтвердить невиданную как новую (просто статус → ACTIVE);
  - alias: связать с существующей категорией (создаём CategoryAlias, переносим
    данные на канон, удаляем дубль). Без авто-fuzzy (I7).
Скоуп — family_id (аудит F1.2).
"""

from __future__ import annotations

from app.kernel import Viewer

from app.contexts.finance.models import Category, CategoryStatus
from app.contexts.finance.repositories import (
    ActualRepository,
    BudgetRepository,
    CategoryRepository,
)
from app.contexts.finance.importing.normalize import normalize_name
from app.contexts.finance.services.errors import (
    CategoryNotFound,
    FamilyRequired,
    InvalidResolution,
)


def _family(viewer: Viewer) -> int:
    if viewer.family_id is None:
        raise FamilyRequired("Сначала создай семью или вступи в неё (/family).")
    return viewer.family_id


class TaxonomyService:
    def __init__(self, session) -> None:
        self.session = session
        self.categories = CategoryRepository(session)
        self.budgets = BudgetRepository(session)
        self.actuals = ActualRepository(session)

    async def list_categories(
        self, viewer: Viewer, status: CategoryStatus | None = None
    ) -> list[Category]:
        return await self.categories.list_all(_family(viewer), status)

    async def review_queue(self, viewer: Viewer) -> list[Category]:
        return await self.categories.list_review(_family(viewer))

    async def accept(self, viewer: Viewer, category_id: int) -> Category:
        """Подтвердить невиданную категорию как новую (NEEDS_REVIEW → ACTIVE)."""
        cat = await self._get(viewer, category_id)
        cat.status = CategoryStatus.ACTIVE
        await self.session.flush()
        return cat

    async def alias(
        self, viewer: Viewer, category_id: int, target_id: int
    ) -> Category:
        """Связать категорию-дубль с каноном: алиас + перенос данных, дубль удаляется.

        Возвращает КАНОН (target). Только в пределах одного уровня дерева (I1/I2):
        корень↔корень или (один родитель)ребёнок↔ребёнок."""
        family_id = _family(viewer)
        dup = await self._get(viewer, category_id)
        target = await self.categories.get(family_id, target_id)
        if target is None:
            raise CategoryNotFound("Целевая категория не найдена")
        if dup.id == target.id:
            raise InvalidResolution("Нельзя связать категорию саму с собой")
        if (dup.parent_id is None) != (target.parent_id is None):
            raise InvalidResolution(
                "Алиас возможен только между категориями одного уровня (I1)"
            )
        if dup.parent_id is not None and dup.parent_id != target.parent_id:
            raise InvalidResolution(
                "Подкатегории должны быть под одним родителем (I2)"
            )
        if await self.categories.count_children(dup.id) > 0:
            raise InvalidResolution(
                "Сначала разрулите подкатегории дубля — у канона будут конфликты"
            )

        # Зарегистрировать алиас (канон для будущих импортов) на нормализованное имя.
        await self.categories.create_alias(
            family_id=family_id,
            alias_name=normalize_name(dup.name),
            category_id=target.id,
        )
        # Перенести план/факт дубля на канон, затем удалить дубль.
        await self._repoint_data(dup.id, target.id)
        await self.session.delete(dup)
        await self.session.flush()
        return target

    async def _repoint_data(self, from_id: int, to_id: int) -> None:
        """Перенести план/факт дубля на канон, СЛИВАЯ совпадающие (month/day) строки.

        Конфликт по unique (category_id, period_month|date) случается, когда у канона
        уже есть строка за тот же месяц/день: суммируем суммы, дублирующую строку
        дубля удаляем; неконфликтные — переадресуем."""
        from decimal import Decimal

        from sqlalchemy import select

        from app.contexts.finance.models import ActualAmount, BudgetPlan

        # ── Планы (по period_month) ──
        canon_plans = {
            p.period_month: p
            for p in (
                await self.session.execute(
                    select(BudgetPlan).where(BudgetPlan.category_id == to_id)
                )
            ).scalars()
        }
        for plan in (
            await self.session.execute(
                select(BudgetPlan).where(BudgetPlan.category_id == from_id)
            )
        ).scalars():
            clash = canon_plans.get(plan.period_month)
            if clash is not None:
                clash.planned_amount = Decimal(clash.planned_amount) + Decimal(
                    plan.planned_amount
                )
                await self.session.delete(plan)
            else:
                plan.category_id = to_id

        # ── Факт (по date) ──
        canon_acts = {
            a.date: a
            for a in (
                await self.session.execute(
                    select(ActualAmount).where(ActualAmount.category_id == to_id)
                )
            ).scalars()
        }
        for act in (
            await self.session.execute(
                select(ActualAmount).where(ActualAmount.category_id == from_id)
            )
        ).scalars():
            clash = canon_acts.get(act.date)
            if clash is not None:
                clash.amount = Decimal(clash.amount) + Decimal(act.amount)
                await self.session.delete(act)
            else:
                act.category_id = to_id
        await self.session.flush()

    async def _get(self, viewer: Viewer, category_id: int) -> Category:
        cat = await self.categories.get(_family(viewer), category_id)
        if cat is None:
            raise CategoryNotFound("Категория не найдена")
        return cat
