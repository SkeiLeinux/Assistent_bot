"""Период агрегации и определение «целых месяцев» (ТЗ §6 I9/I10, §13).

Каденс D/W/M/Q/Y + произвольный диапазон. Ключевые правила:
  - I9: план/разница заполняются ТОЛЬКО когда диапазон состоит из ЦЕЛЫХ календарных
    месяцев (выровнен по границам месяцев). Иначе plan=None (UI показывает только факт).
  - I10: доходы агрегируются только по месяцам, ЦЕЛИКОМ входящим в диапазон.
Pro-rata не делается. Всё на чистом Python (объём крошечный, портируемость дат).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Period:
    """Разрешённый диапазон агрегации [date_from, date_to] (обе границы включительно)."""

    label: str  # "D"|"W"|"M"|"Q"|"Y"|"custom"
    date_from: date
    date_to: date


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def whole_months(date_from: date, date_to: date) -> list[date]:
    """Список первых-дней-месяцев, ЦЕЛИКОМ лежащих в [date_from, date_to] (I9/I10).

    Месяц целиком в диапазоне ⟺ его первый день ≥ date_from и последний день ≤ date_to.
    """
    out: list[date] = []
    cursor = _month_start(date_from)
    # Если date_from не первое число — первый месяц неполный, начинаем со следующего.
    if cursor < date_from:
        cursor = _next_month(cursor)
    while cursor <= date_to:
        last = date(cursor.year, cursor.month, _last_day_of_month(cursor.year, cursor.month))
        if last <= date_to:
            out.append(cursor)
        cursor = _next_month(cursor)
    return out


def is_whole_month_range(date_from: date, date_to: date) -> bool:
    """Диапазон выровнен по границам месяцев (I9): начинается 1-м, кончается последним,
    и каждый промежуточный месяц целиком входит → план/разница применимы."""
    if date_from.day != 1:
        return False
    if date_to.day != _last_day_of_month(date_to.year, date_to.month):
        return False
    months = whole_months(date_from, date_to)
    if not months:
        return False
    # Непрерывность: число целых месяцев = число месяцев между границами.
    expected = (date_to.year - date_from.year) * 12 + (date_to.month - date_from.month) + 1
    return len(months) == expected


def month_period(period_month: date) -> Period:
    """Целый календарный месяц по его первому дню."""
    last = _last_day_of_month(period_month.year, period_month.month)
    return Period("M", date(period_month.year, period_month.month, 1),
                  date(period_month.year, period_month.month, last))


def quarter_period(year: int, quarter: int) -> Period:
    if quarter not in (1, 2, 3, 4):
        raise ValueError("квартал 1..4")
    first_month = (quarter - 1) * 3 + 1
    last_month = first_month + 2
    last = _last_day_of_month(year, last_month)
    return Period("Q", date(year, first_month, 1), date(year, last_month, last))


def year_period(year: int) -> Period:
    return Period("Y", date(year, 1, 1), date(year, 12, 31))


def custom_period(date_from: date, date_to: date) -> Period:
    if date_to < date_from:
        raise ValueError("date_to раньше date_from")
    return Period("custom", date_from, date_to)
