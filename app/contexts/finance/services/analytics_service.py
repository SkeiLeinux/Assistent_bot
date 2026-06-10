"""AnalyticsService — план/факт/разница/% + иерархия + каденс + доходы (ТЗ §6/§10).

Каденс D/W/M/Q/Y + произвольный период (S5). Применяет I9 (plan=None вне целых
месяцев) и I10 (доходы только по целым месяцам периода) через `periods` + `read`.
Сводные (Доход/Расход/Дельта) выводятся здесь (I5), не хранятся. Standalone-шов:
принимает резолвнутого Viewer (family_id), Telegram-агностичен.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.kernel import Viewer

from app.contexts.finance.read import (
    FinanceReadResult,
    IncomeAggregate,
    daily_rows,
    finance_aggregate,
)
from app.contexts.finance.services.errors import FamilyRequired, InvalidInput
from app.contexts.finance.services.periods import (
    Period,
    custom_period,
    is_whole_month_range,
    month_period,
    quarter_period,
    whole_months,
    year_period,
)


@dataclass
class AnalyticsLine:
    category_id: int
    name: str
    plan: Decimal | None
    fact: Decimal
    diff: Decimal | None
    pct: float | None
    children: list["AnalyticsLine"]


@dataclass
class AnalyticsResult:
    period: str
    date_from: date
    date_to: date
    plan_applicable: bool
    total_plan: Decimal | None
    total_fact: Decimal
    total_diff: Decimal | None
    lines: list[AnalyticsLine]


@dataclass
class IncomeReport:
    date_from: date
    date_to: date
    by_source: list[IncomeAggregate]
    carryover: Decimal
    total_income: Decimal
    total_expense: Decimal
    delta: Decimal


def _family(viewer: Viewer) -> int:
    if viewer.family_id is None:
        raise FamilyRequired("Сначала создай семью или вступи в неё (/family).")
    return viewer.family_id


def resolve_period(
    period: str | None,
    *,
    month: date | None = None,
    quarter: int | None = None,
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Period:
    """Параметры запроса → Period. Поддержка M/Q/Y по конкретному месяцу/кварталу/году
    и произвольный диапазон (D/W — частный случай custom)."""
    p = (period or "").upper()
    if p == "M":
        if month is None:
            raise InvalidInput("Для period=M нужен month=YYYY-MM-01")
        return month_period(month)
    if p == "Q":
        if quarter is None or year is None:
            raise InvalidInput("Для period=Q нужны year и quarter (1..4)")
        try:
            return quarter_period(year, quarter)
        except ValueError as e:
            raise InvalidInput(str(e))
    if p == "Y":
        if year is None:
            raise InvalidInput("Для period=Y нужен year")
        return year_period(year)
    # D/W/custom — произвольный диапазон по дням.
    if date_from is None or date_to is None:
        raise InvalidInput("Нужны from и to (или period=M/Q/Y с параметрами)")
    try:
        rng = custom_period(date_from, date_to)
    except ValueError as e:
        raise InvalidInput(str(e))
    label = p if p in ("D", "W") else "custom"
    return Period(label, rng.date_from, rng.date_to)


def _pct(fact: Decimal, plan: Decimal | None) -> float | None:
    """Доля факта от плана (как в таблице, колонка %). None если план не применим/0."""
    if plan is None or plan == 0:
        return None
    return float(fact / plan)


class AnalyticsService:
    def __init__(self, session) -> None:
        self.session = session

    async def _read(self, viewer: Viewer, period: Period) -> FinanceReadResult:
        family_id = _family(viewer)
        plan_ok = is_whole_month_range(period.date_from, period.date_to)
        months = whole_months(period.date_from, period.date_to)
        return await finance_aggregate(
            self.session,
            family_id,
            period.date_from,
            period.date_to,
            plan_applicable=plan_ok,
            whole_months=months,
        )

    async def analytics(
        self, viewer: Viewer, period: Period, category_id: int | None = None
    ) -> AnalyticsResult:
        res = await self._read(viewer, period)
        by_id = {a.category_id: a for a in res.categories}
        children: dict[int, list] = {}
        roots = []
        for a in res.categories:
            if a.parent_id is None:
                roots.append(a)
            else:
                children.setdefault(a.parent_id, []).append(a)

        def build(agg) -> AnalyticsLine:
            kids = [build(c) for c in children.get(agg.category_id, [])]
            plan = agg.plan if res.plan_applicable else None
            diff = (plan - agg.fact) if plan is not None else None
            return AnalyticsLine(
                category_id=agg.category_id,
                name=agg.name,
                plan=plan,
                fact=agg.fact,
                diff=diff,
                pct=_pct(agg.fact, plan),
                children=kids,
            )

        if category_id is not None:
            # Drill-down: поддерево конкретной категории.
            target = by_id.get(category_id)
            if target is None:
                lines: list[AnalyticsLine] = []
            else:
                lines = [build(target)]
        else:
            lines = [build(r) for r in roots]

        total_fact = sum((r.fact for r in roots), Decimal("0"))
        total_plan: Decimal | None = None
        total_diff: Decimal | None = None
        if res.plan_applicable:
            total_plan = sum(
                (a.plan for a in res.categories if a.plan is not None and a.parent_id is None),
                Decimal("0"),
            )
            total_diff = total_plan - total_fact

        return AnalyticsResult(
            period=period.label,
            date_from=period.date_from,
            date_to=period.date_to,
            plan_applicable=res.plan_applicable,
            total_plan=total_plan,
            total_fact=total_fact,
            total_diff=total_diff,
            lines=lines,
        )

    async def export_xlsx(self, viewer: Viewer, period: Period) -> bytes:
        """Выгрузка периода в xlsx (A7): «Сводка» (аналитика) + «По дням» (факт листьев).

        openpyxl заперт в `exporting/` — импорт лениво внутри метода (A6)."""
        from app.contexts.finance.exporting import export_period

        family_id = _family(viewer)
        result = await self.analytics(viewer, period)
        read_res = await self._read(viewer, period)
        rows = await daily_rows(
            self.session, family_id, period.date_from, period.date_to
        )
        return export_period(result, read_res.categories, rows)

    async def income_report(self, viewer: Viewer, period: Period) -> IncomeReport:
        """Доходы по источникам/партнёрам + переходящий остаток + Дельта (S4, §10).

        Доходы — только за целые месяцы периода (I10). Расход = Σ факт листьев за
        ВЕСЬ диапазон по дням (факт всегда доступен). Дельта = доход − расход (I5)."""
        res = await self._read(viewer, period)
        total_income = sum(
            (s.fact for s in res.income if s.fact is not None), Decimal("0")
        )
        carryover = sum(
            (s.fact for s in res.income if s.kind == "carryover" and s.fact is not None),
            Decimal("0"),
        )
        # Расход = Σ факт листьев (родители — агрегаты, не дублируем).
        total_expense = sum(
            (a.fact for a in res.categories if a.parent_id is not None), Decimal("0")
        )
        return IncomeReport(
            date_from=period.date_from,
            date_to=period.date_to,
            by_source=res.income,
            carryover=carryover,
            total_income=total_income,
            total_expense=total_expense,
            delta=total_income - total_expense,
        )
