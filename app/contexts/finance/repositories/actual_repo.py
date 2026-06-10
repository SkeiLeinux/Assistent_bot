"""Репозиторий факта (ActualAmount) — дневная гранулярность на листьях (ТЗ §4.4).

Идемпотентность по месяцу (I6): `delete_month` сносит дневные строки категорий
семьи за весь месяц перед перезаписью. Запросы агрегации (план/факт/каденс) —
группировка по периоду в Python (read-модуль), здесь — только хранение/выборка.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finance.models import ActualAmount, Category


def _month_bounds(period_month: date) -> tuple[date, date]:
    """[первый день, первый день следующего месяца)."""
    if period_month.month == 12:
        nxt = date(period_month.year + 1, 1, 1)
    else:
        nxt = date(period_month.year, period_month.month + 1, 1)
    return date(period_month.year, period_month.month, 1), nxt


class ActualRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_month(self, family_id: int, period_month: date) -> None:
        start, end = _month_bounds(period_month)
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        await self.session.execute(
            delete(ActualAmount).where(
                ActualAmount.category_id.in_(cat_ids),
                ActualAmount.date >= start,
                ActualAmount.date < end,
            )
        )

    async def add(
        self, *, category_id: int, day: date, amount: Decimal
    ) -> ActualAmount:
        row = ActualAmount(category_id=category_id, date=day, amount=amount)
        self.session.add(row)
        return row

    async def in_range(
        self, family_id: int, date_from: date, date_to: date
    ) -> list[ActualAmount]:
        """Все дневные строки семьи в диапазоне [from, to] включительно."""
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        q = select(ActualAmount).where(
            ActualAmount.category_id.in_(cat_ids),
            ActualAmount.date >= date_from,
            ActualAmount.date <= date_to,
        )
        return list((await self.session.execute(q)).scalars().all())

    async def imported_months(self, family_id: int) -> list[date]:
        """Список месяцев (первый день), за которые есть факт — для GET /months."""
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        q = select(ActualAmount.date).where(ActualAmount.category_id.in_(cat_ids))
        days = (await self.session.execute(q)).scalars().all()
        months = {date(d.year, d.month, 1) for d in days}
        return sorted(months)

    async def has_facts_for_month(self, family_id: int, period_month: date) -> bool:
        start, end = _month_bounds(period_month)
        cat_ids = select(Category.id).where(Category.family_id == family_id)
        q = select(ActualAmount.id).where(
            ActualAmount.category_id.in_(cat_ids),
            and_(ActualAmount.date >= start, ActualAmount.date < end),
        ).limit(1)
        return (await self.session.execute(q)).first() is not None

    async def months_with_any_data(
        self, family_id: int, plan_months: list[date]
    ) -> list[date]:
        """Объединение месяцев с фактом и месяцев с планом (для GET /months)."""
        fact_months = set(await self.imported_months(family_id))
        return sorted(fact_months | set(plan_months))
