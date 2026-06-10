"""Репозиторий планов (BudgetPlan) — per-category на месяц (ТЗ §4.3).

Идемпотентность импорта по месяцу (I6): `delete_month` сносит планы категорий
семьи за месяц перед перезаписью. Скоуп — через join на Category.family_id.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finance.models import BudgetPlan, Category


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_month(self, family_id: int, period_month: date) -> None:
        """Удалить планы всех категорий семьи за месяц (идемпотентный re-import)."""
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        await self.session.execute(
            delete(BudgetPlan).where(
                BudgetPlan.category_id.in_(cat_ids),
                BudgetPlan.period_month == period_month,
            )
        )

    async def add(
        self, *, category_id: int, period_month: date, planned_amount: Decimal
    ) -> BudgetPlan:
        plan = BudgetPlan(
            category_id=category_id,
            period_month=period_month,
            planned_amount=planned_amount,
        )
        self.session.add(plan)
        return plan

    async def for_months(
        self, family_id: int, months: list[date]
    ) -> list[BudgetPlan]:
        if not months:
            return []
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        q = select(BudgetPlan).where(
            BudgetPlan.category_id.in_(cat_ids),
            BudgetPlan.period_month.in_(months),
        )
        return list((await self.session.execute(q)).scalars().all())
