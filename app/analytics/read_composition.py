"""read_composition — кросс-сферный обзор поверх contexts.*.read (ТЗ §6/§9).

Единый read-обзор (OverviewDTO) композирует доменные read-проекции; своих агрегаций
не пересчитывает (ТЗ §6, I1). Доступ к доменам — ТОЛЬКО через `contexts.*.read`
(I2/A5, import-linter). Видимость обеспечивают сами read-проекции (kernel-клаузы):
списки/финансы — FAMILY, питание — PERSONAL по viewer.id (I3/A4).

Закрытые точки парковки (docs/.../questions/analytics.md):
  Q-an-1 — секция списков идёт через lists.read.recent_changes (added/done по типам
           за период, одним проходом); обход через analytics_by_type снят.
  Q-an-2 — цели КБЖУ берутся через nutrition.read.goals → nutrition.goal (факт vs цель).
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer

# Доменные данные — СТРОГО через read-модули (A5, import-linter).
from app.contexts.lists.read import recent_changes
from app.contexts.finance.read import finance_aggregate
from app.contexts.nutrition.read import goals, macros_total


# ── Вспомогательное: целые месяцы в диапазоне (для finance plan/income) ────────

def whole_months_in_range(date_from: date, date_to: date) -> list[date]:
    """Первые числа КАЛЕНДАРНЫХ месяцев, целиком лежащих в [date_from, date_to].

    Finance.read ждёт `whole_months`/`plan_applicable` от вызывающего (read — чистая
    проекция без знания каденсов). План/доходы применимы только за целые месяцы (I9/I10
    Finance) — считаем их здесь, не импортируя finance.services (изоляция A5).
    """
    months: list[date] = []
    y, m = date_from.year, date_from.month
    while (y, m) <= (date_to.year, date_to.month):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        if first >= date_from and last <= date_to:
            months.append(first)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _plan_applicable(date_from: date, date_to: date, whole_months: list[date]) -> bool:
    """План применим, если диапазон = непрерывная последовательность целых месяцев."""
    if not whole_months:
        return False
    first = whole_months[0]
    last_first = whole_months[-1]
    last_day = date(last_first.year, last_first.month,
                    calendar.monthrange(last_first.year, last_first.month)[1])
    return date_from == first and date_to == last_day


# ── Per-sphere обзоры (каждый — поверх read; visibility внутри проекций) ───────

async def lists_overview(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date
) -> dict:
    """Списки: добавлено/выполнено за период по типам (Q-an-1, через recent_changes).

    recent_changes отдаёт только типы с ненулевой активностью; `type` — строка
    (.value), visibility-фильтровано (скрытые/невидимые исключены, I3/A4).
    """
    changes = await recent_changes(session, viewer, date_from, date_to)
    by_type = [
        {"type": c["type"], "added": c["added"], "done": c["done"]}
        for c in changes
    ]
    return {"by_type": by_type}


async def finance_overview(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date
) -> dict:
    """Финансы: план/факт/разница + top-отклонения, через finance.read."""
    if viewer.family_id is None:
        return {"plan": None, "fact": 0.0, "diff": None, "top_deviations": []}

    months = whole_months_in_range(date_from, date_to)
    plan_applicable = _plan_applicable(date_from, date_to, months)
    result = await finance_aggregate(
        session, viewer.family_id, date_from, date_to,
        plan_applicable=plan_applicable, whole_months=months,
    )

    roots = [c for c in result.categories if c.parent_id is None]
    total_fact = sum((c.fact for c in roots), Decimal("0"))
    total_plan = (
        sum((c.plan or Decimal("0") for c in roots), Decimal("0"))
        if plan_applicable else None
    )
    total_diff = (total_plan - total_fact) if total_plan is not None else None

    # top-отклонения план/факт по корневым категориям (по модулю разницы).
    deviations = []
    if plan_applicable:
        for c in roots:
            if c.plan is None:
                continue
            diff = c.plan - c.fact
            deviations.append({
                "category": c.name,
                "plan": float(c.plan),
                "fact": float(c.fact),
                "diff": float(diff),
            })
        deviations.sort(key=lambda d: abs(d["diff"]), reverse=True)

    return {
        "plan": float(total_plan) if total_plan is not None else None,
        "fact": float(total_fact),
        "diff": float(total_diff) if total_diff is not None else None,
        "plan_applicable": plan_applicable,
        "top_deviations": deviations[:3],
    }


async def nutrition_overview(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date,
    tz_name: str | None,
) -> dict:
    """Питание: КБЖУ владельца за период (PERSONAL) + цель КБЖУ (Q-an-2).

    Цель — через nutrition.read.goals (PERSONAL по viewer.id). Нет строки целей →
    goal=None (поведение как раньше: только факт).
    """
    totals = await macros_total(session, viewer, date_from, date_to, tz_name)
    goal = await goals(session, viewer)
    return {
        "kcal": totals["kcal"],
        "protein": totals["protein"],
        "fat": totals["fat"],
        "carbs": totals["carbs"],
        "days_with_data": totals["days_with_data"],
        "goal": goal,  # {kcal,protein,fat,carbs} (поля могут быть None) либо None
    }


# ── Сборка OverviewDTO (ТЗ §9) ────────────────────────────────────────────────

async def build_overview(
    session: AsyncSession,
    viewer: Viewer,
    date_from: date,
    date_to: date,
    tz_name: str | None = None,
) -> dict:
    """OverviewDTO = {range, lists, finance, nutrition} — всё on-the-fly, по видимости."""
    return {
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "lists": await lists_overview(session, viewer, date_from, date_to),
        "finance": await finance_overview(session, viewer, date_from, date_to),
        "nutrition": await nutrition_overview(
            session, viewer, date_from, date_to, tz_name
        ),
    }
