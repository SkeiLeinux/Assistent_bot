"""Read-проекции Nutrition для аналитики (ТЗ §10/§13 A6).

Единственная read-граница контекста: Analytics (S3) берёт КБЖУ ТОЛЬКО отсюда
(import-linter запрещает прямой импорт models/repositories/services/api из
analytics). LLM-специфика сюда не протекает.

Видимость (ТЗ §6 I4): дневник PERSONAL — все проекции фильтруются по владельцу
(`viewer.id`); чужой дневник не виден. Группировка по дням — в Python в таймзоне
пользователя (объём крошечный, req §9; не зависим от DB-специфики дат, ADR-0005).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import Viewer

from app.contexts.nutrition.models import FoodEntry, NutritionGoals

_DEFAULT_TZ = "Europe/Amsterdam"


@dataclass(frozen=True)
class MacroTotals:
    """Суммарные КБЖУ за интервал (абсолют, I1: сумма за период = SUM)."""

    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0


def _tz(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or _DEFAULT_TZ)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _bounds_utc(tz: ZoneInfo, day: date) -> datetime:
    """Начало локального дня → наивный UTC."""
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)


async def _visible_entries(
    session: AsyncSession,
    viewer: Viewer,
    start_utc: datetime,
    end_utc: datetime,
) -> list[FoodEntry]:
    """Видимые зрителю записи дневника в [start, end) по UTC (PERSONAL-фильтр)."""
    result = await session.execute(
        select(FoodEntry)
        .where(
            FoodEntry.user_id == viewer.id,  # PERSONAL: только владелец (I4)
            FoodEntry.created_at >= start_utc,
            FoodEntry.created_at < end_utc,
        )
        .order_by(FoodEntry.created_at)
    )
    return list(result.scalars().all())


async def macros_by_day(
    session: AsyncSession,
    viewer: Viewer,
    date_from: date,
    date_to: date,
    timezone_name: str | None = None,
) -> list[dict]:
    """КБЖУ по дням за [date_from, date_to] включительно, в таймзоне пользователя.

    Возвращает по записи на каждый день, где есть приёмы пищи (пустые дни
    пропускаются), отсортированные по дате. Для дайджеста/трендов Analytics.
    """
    tz = _tz(timezone_name)
    start_utc = _bounds_utc(tz, date_from)
    end_utc = _bounds_utc(tz, date_to + timedelta(days=1))
    entries = await _visible_entries(session, viewer, start_utc, end_utc)

    by_day: dict[date, MacroTotals] = {}
    for e in entries:
        local = e.created_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
        cur = by_day.get(local, MacroTotals())
        by_day[local] = MacroTotals(
            kcal=cur.kcal + e.kcal,
            protein=cur.protein + e.protein,
            fat=cur.fat + e.fat,
            carbs=cur.carbs + e.carbs,
        )
    return [
        {
            "date": d.isoformat(),
            "kcal": round(by_day[d].kcal, 1),
            "protein": round(by_day[d].protein, 1),
            "fat": round(by_day[d].fat, 1),
            "carbs": round(by_day[d].carbs, 1),
        }
        for d in sorted(by_day)
    ]


async def macros_total(
    session: AsyncSession,
    viewer: Viewer,
    date_from: date,
    date_to: date,
    timezone_name: str | None = None,
) -> dict:
    """Суммарные КБЖУ за период [date_from, date_to] включительно + число дней с данными."""
    days = await macros_by_day(session, viewer, date_from, date_to, timezone_name)
    total = MacroTotals(
        kcal=sum(d["kcal"] for d in days),
        protein=sum(d["protein"] for d in days),
        fat=sum(d["fat"] for d in days),
        carbs=sum(d["carbs"] for d in days),
    )
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "days_with_data": len(days),
        "kcal": round(total.kcal, 1),
        "protein": round(total.protein, 1),
        "fat": round(total.fat, 1),
        "carbs": round(total.carbs, 1),
    }


def _round_opt(value: float | None) -> float | None:
    """Округление как в macros_total; None (поле цели не задано) пробрасывается."""
    return None if value is None else round(value, 1)


async def goals(session: AsyncSession, viewer: Viewer) -> dict | None:
    """Дневные цели КБЖУ владельца (`NutritionGoals` 1:1 по `user_id`).

    Для виджета «факт vs цель» в Analytics (закрытие Q-an-2): даёт цель той же
    формы, что и `macros_total` (`{"kcal","protein","fat","carbs"}`, округление до
    0.1). Возвращает `None`, если строка целей не заведена.

    Видимость (ТЗ §6 I4): цели PERSONAL — фильтр строго по `viewer.id` (как
    `_visible_entries`); чужие цели не видны. Отдельные поля могут быть `None`
    (цель по нутриенту не задана) — это не то же, что отсутствие строки целиком.
    """
    result = await session.execute(
        select(NutritionGoals).where(
            NutritionGoals.user_id == viewer.id  # PERSONAL: только владелец (I4)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {
        "kcal": _round_opt(row.target_kcal),
        "protein": _round_opt(row.target_protein),
        "fat": _round_opt(row.target_fat),
        "carbs": _round_opt(row.target_carbs),
    }
