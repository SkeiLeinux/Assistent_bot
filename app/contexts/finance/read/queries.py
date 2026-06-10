"""Read-проекции Finance для Analytics (ТЗ §12, A5/A6).

Единственная read-граница контекста: Analytics (S3) берёт агрегаты ТОЛЬКО отсюда
(import-linter запрещает прямой импорт models/repositories/services из analytics).
Импортирует только kernel/db — модели разрешены (они в db.metadata, read проецирует
их). Все выборки скоупятся `family_id` (весь Finance FAMILY, аудит F1.2). Агрегация
по периоду — в Python (объём крошечный, портируемость дат, ADR-0005).

Возвращает «сырые» агрегаты (план/факт по категориям + доходы) — форму AnalyticsDTO
собирает сервис/REST. Здесь — данные, не представление.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finance.models import (
    ActualAmount,
    BudgetPlan,
    Category,
    Income,
    IncomeSource,
)


@dataclass
class CategoryAggregate:
    """Агрегат по категории за период: план (если применим) и факт."""

    category_id: int
    name: str
    parent_id: int | None
    plan: Decimal | None  # None вне целых месяцев (I9)
    fact: Decimal


@dataclass
class IncomeAggregate:
    source_id: int
    source_name: str
    kind: str
    user_id: int | None
    plan: Decimal | None
    fact: Decimal | None


@dataclass
class FinanceReadResult:
    """Сырой результат read для Analytics: категории + доходы за период."""

    date_from: date
    date_to: date
    plan_applicable: bool
    categories: list[CategoryAggregate] = field(default_factory=list)
    income: list[IncomeAggregate] = field(default_factory=list)


async def _categories(session: AsyncSession, family_id: int) -> list[Category]:
    q = select(Category).where(Category.family_id == family_id).order_by(
        Category.parent_id.is_(None).desc(), Category.id
    )
    return list((await session.execute(q)).scalars().all())


async def _fact_by_category(
    session: AsyncSession, family_id: int, date_from: date, date_to: date
) -> dict[int, Decimal]:
    """Σ факт по category_id в диапазоне дней [from, to] (факт на листьях)."""
    cat_ids = select(Category.id).where(Category.family_id == family_id)
    q = select(ActualAmount.category_id, ActualAmount.amount).where(
        ActualAmount.category_id.in_(cat_ids),
        and_(ActualAmount.date >= date_from, ActualAmount.date <= date_to),
    )
    out: dict[int, Decimal] = {}
    for cat_id, amount in (await session.execute(q)).all():
        out[cat_id] = out.get(cat_id, Decimal("0")) + Decimal(amount)
    return out


async def daily_rows(
    session: AsyncSession, family_id: int, date_from: date, date_to: date
) -> list[tuple[int, date, Decimal]]:
    """Сырые дневные строки листьев в диапазоне (для xlsx-выгрузки «По дням», A7)."""
    cat_ids = select(Category.id).where(Category.family_id == family_id)
    q = (
        select(ActualAmount.category_id, ActualAmount.date, ActualAmount.amount)
        .where(
            ActualAmount.category_id.in_(cat_ids),
            and_(ActualAmount.date >= date_from, ActualAmount.date <= date_to),
        )
        .order_by(ActualAmount.date)
    )
    return [(cid, d, Decimal(a)) for cid, d, a in (await session.execute(q)).all()]


async def _plan_by_category(
    session: AsyncSession, family_id: int, months: list[date]
) -> dict[int, Decimal]:
    """Σ план по category_id за указанные целые месяцы (план per-category, I4)."""
    if not months:
        return {}
    cat_ids = select(Category.id).where(Category.family_id == family_id)
    q = select(BudgetPlan.category_id, BudgetPlan.planned_amount).where(
        BudgetPlan.category_id.in_(cat_ids),
        BudgetPlan.period_month.in_(months),
    )
    out: dict[int, Decimal] = {}
    for cat_id, amount in (await session.execute(q)).all():
        out[cat_id] = out.get(cat_id, Decimal("0")) + Decimal(amount)
    return out


async def _income(
    session: AsyncSession, family_id: int, months: list[date]
) -> list[IncomeAggregate]:
    """Доходы за ЦЕЛЫЕ месяцы периода (I10). Σ план/факт по источнику."""
    if not months:
        return []
    q = (
        select(Income, IncomeSource)
        .join(IncomeSource, Income.source_id == IncomeSource.id)
        .where(IncomeSource.family_id == family_id, Income.period_month.in_(months))
    )
    acc: dict[int, IncomeAggregate] = {}
    for inc, src in (await session.execute(q)).all():
        agg = acc.get(src.id)
        if agg is None:
            agg = IncomeAggregate(
                source_id=src.id,
                source_name=src.name,
                kind=src.kind.value,
                user_id=src.user_id,
                plan=None,
                fact=None,
            )
            acc[src.id] = agg
        if inc.planned_amount is not None:
            agg.plan = (agg.plan or Decimal("0")) + Decimal(inc.planned_amount)
        if inc.actual_amount is not None:
            agg.fact = (agg.fact or Decimal("0")) + Decimal(inc.actual_amount)
    return list(acc.values())


async def finance_aggregate(
    session: AsyncSession,
    family_id: int,
    date_from: date,
    date_to: date,
    *,
    plan_applicable: bool,
    whole_months: list[date],
) -> FinanceReadResult:
    """Главная read-проекция: план(если применим)/факт по категориям + доходы.

    `plan_applicable` (I9) и `whole_months` (I9/I10) вычисляются сервисом периодов и
    передаются сюда — read остаётся чистой проекцией без знания о каденсах.
    """
    cats = await _categories(session, family_id)
    facts = await _fact_by_category(session, family_id, date_from, date_to)
    plans = (
        await _plan_by_category(session, family_id, whole_months)
        if plan_applicable
        else {}
    )

    # Факт родителя — агрегат фактов детей (на родителе факта нет, §4.4).
    children_of: dict[int, list[Category]] = {}
    for c in cats:
        if c.parent_id is not None:
            children_of.setdefault(c.parent_id, []).append(c)

    aggregates: list[CategoryAggregate] = []
    for c in cats:
        if c.parent_id is None:
            fact = sum(
                (facts.get(ch.id, Decimal("0")) for ch in children_of.get(c.id, [])),
                Decimal("0"),
            )
        else:
            fact = facts.get(c.id, Decimal("0"))
        plan = plans.get(c.id) if plan_applicable else None
        aggregates.append(
            CategoryAggregate(
                category_id=c.id,
                name=c.name,
                parent_id=c.parent_id,
                plan=plan,
                fact=fact,
            )
        )

    income = await _income(session, family_id, whole_months)
    return FinanceReadResult(
        date_from=date_from,
        date_to=date_to,
        plan_applicable=plan_applicable,
        categories=aggregates,
        income=income,
    )
