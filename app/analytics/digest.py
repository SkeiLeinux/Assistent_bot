"""digest — ретро-дайджест per-recipient (ТЗ §7, I4/I5).

`compose_digest(recipient, период)` собирает сводку под конкретного получателя:
Списки + Финансы + Питание (БЕЗ напоминаний — I5/A3), за период [date_from, date_to].
Видимость (I3/A4) обеспечивают read-проекции (kernel-клаузы): списки/финансы FAMILY,
питание PERSONAL по viewer.id. Пустые секции скрываются; полностью пустой дайджест →
compose возвращает None (не шлём, ТЗ §11).

Источник «изменений списков» — lists.read.recent_changes по полям ListItem (Q1-А10/
CH-5): секция списков = добавлено/выполнено за период по типам (Q-an-1 закрыт). Цель
КБЖУ — через nutrition.read.goals (Q-an-2 закрыт). «Крупные траты» = top-3 расходов по
сумме без порога (Q1-Б4 / CH-7, обход Q-an-3). Усечение >4096 — _truncate_telegram
(CH-8, обход Q-an-4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer

from app.contexts.finance.read import daily_rows, finance_aggregate
from app.contexts.nutrition.read import goals, macros_total

from app.analytics.read_composition import (
    lists_overview,
    whole_months_in_range,
    _plan_applicable,
)

_TELEGRAM_LIMIT = 4096
_RU_TYPE = {
    "task": "Дела",
    "shopping": "Покупки",
    "idea": "Идеи",
    "movie": "Фильмы",
    "gift": "Подарки",
    "link": "Ссылки",
}


# ── Секции (каждая возвращает dict или None, если пуста) ──────────────────────

async def _lists_section(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date
) -> dict | None:
    """Списки: типы с активностью за период — добавлено/выполнено (Q-an-1).

    recent_changes уже отдаёт только активные типы (added>0 или done>0),
    visibility-фильтровано (kernel-клаузы). Пусто → секция скрыта (None).
    """
    overview = await lists_overview(session, viewer, date_from, date_to)
    changed = [t for t in overview["by_type"] if t["added"] > 0 or t["done"] > 0]
    if not changed:
        return None
    return {"sphere": "lists", "by_type": changed}


async def _finance_section(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date
) -> dict | None:
    """Финансы: top-3 трат периода по сумме (CH-7) + сводный факт/план."""
    if viewer.family_id is None:
        return None

    months = whole_months_in_range(date_from, date_to)
    plan_applicable = _plan_applicable(date_from, date_to, months)
    result = await finance_aggregate(
        session, viewer.family_id, date_from, date_to,
        plan_applicable=plan_applicable, whole_months=months,
    )

    # top-3 трат: суммируем дневные строки листьев по категории, имена — из result.
    rows = await daily_rows(session, viewer.family_id, date_from, date_to)
    if not rows:
        return None
    name_by_id = {c.category_id: c.name for c in result.categories}
    by_cat: dict[int, Decimal] = {}
    for cat_id, _day, amount in rows:
        by_cat[cat_id] = by_cat.get(cat_id, Decimal("0")) + amount
    top = sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_expenses = [
        {"category": name_by_id.get(cat_id, f"#{cat_id}"), "amount": float(amount)}
        for cat_id, amount in top
    ]

    roots = [c for c in result.categories if c.parent_id is None]
    total_fact = float(sum((c.fact for c in roots), Decimal("0")))
    total_plan = (
        float(sum((c.plan or Decimal("0") for c in roots), Decimal("0")))
        if plan_applicable else None
    )
    return {
        "sphere": "finance",
        "fact": total_fact,
        "plan": total_plan,
        "top_expenses": top_expenses,
    }


async def _nutrition_section(
    session: AsyncSession, viewer: Viewer, date_from: date, date_to: date,
    tz_name: str | None,
) -> dict | None:
    """Питание: личная сводка КБЖУ получателя (PERSONAL, I4) + цель (Q-an-2).

    Цель — через nutrition.read.goals (PERSONAL по viewer.id). Нет целей → goal=None
    (как раньше: показываем только факт). Пусто (нет данных за период) → None.
    """
    totals = await macros_total(session, viewer, date_from, date_to, tz_name)
    if totals["days_with_data"] == 0:
        return None
    goal = await goals(session, viewer)
    return {
        "sphere": "nutrition",
        "kcal": totals["kcal"],
        "protein": totals["protein"],
        "fat": totals["fat"],
        "carbs": totals["carbs"],
        "days_with_data": totals["days_with_data"],
        "goal": goal,  # {kcal,protein,fat,carbs} (поля могут быть None) либо None
    }


# ── Композиция ────────────────────────────────────────────────────────────────

async def compose_digest(
    session: AsyncSession,
    viewer: Viewer,
    date_from: date,
    date_to: date,
    tz_name: str | None = None,
) -> dict | None:
    """DigestDTO per-recipient или None, если показывать нечего (ТЗ §7/§11).

    Состав = Списки+Финансы+Питание (БЕЗ напоминаний, I5). Пустые секции опущены;
    полностью пустой → None (не шлём).
    """
    sections = []
    for builder in (
        await _lists_section(session, viewer, date_from, date_to),
        await _finance_section(session, viewer, date_from, date_to),
        await _nutrition_section(session, viewer, date_from, date_to, tz_name),
    ):
        if builder is not None:
            sections.append(builder)

    if not sections:
        return None

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "sections": sections,
    }


# ── Рендер в текст для Notifications (ru, HTML-безопасно) ──────────────────────

def _render_section(section: dict) -> list[str]:
    sphere = section["sphere"]
    lines: list[str] = []
    if sphere == "lists":
        lines.append("<b>Списки</b>")
        for t in section["by_type"]:
            label = _RU_TYPE.get(t["type"], t["type"])
            parts = []
            if t["added"] > 0:
                parts.append(f"добавлено {t['added']}")
            if t["done"] > 0:
                parts.append(f"выполнено {t['done']}")
            lines.append(f"• {label}: " + ", ".join(parts))
    elif sphere == "finance":
        lines.append("<b>Финансы</b>")
        if section["plan"] is not None:
            lines.append(f"• Факт {section['fact']:.0f} ₽ из плана {section['plan']:.0f} ₽")
        else:
            lines.append(f"• Расходы {section['fact']:.0f} ₽")
        if section["top_expenses"]:
            lines.append("• Крупные траты:")
            for e in section["top_expenses"]:
                lines.append(f"   – {e['category']}: {e['amount']:.0f} ₽")
    elif sphere == "nutrition":
        lines.append("<b>Питание</b>")
        goal = section.get("goal")
        kcal_goal = goal.get("kcal") if goal else None
        if kcal_goal is not None:
            kcal_line = (
                f"• {section['kcal']:.0f} / {kcal_goal:.0f} ккал "
                f"за {section['days_with_data']} дн. "
            )
        else:
            kcal_line = f"• {section['kcal']:.0f} ккал за {section['days_with_data']} дн. "
        lines.append(
            kcal_line
            + f"(Б {section['protein']:.0f} / Ж {section['fat']:.0f} / У {section['carbs']:.0f})"
        )
    return lines


def render_text(dto: dict, recipient_name: str | None = None) -> str:
    """Текст дайджеста для push (ru). Усечён до лимита Telegram (CH-8/Q-an-4)."""
    period = dto["period"]
    head = "📊 <b>Дайджест</b>"
    if recipient_name:
        head += f" — {recipient_name}"
    head += f"\n{period['from']} — {period['to']}"

    blocks = [head]
    for section in dto["sections"]:
        blocks.append("\n".join(_render_section(section)))
    text = "\n\n".join(blocks)
    return _truncate_telegram(text)


def _truncate_telegram(text: str, limit: int = _TELEGRAM_LIMIT) -> str:
    """Усечь по строкам с хвостом «…и ещё N строк» (CH-8, обход Q-an-4).

    На несколько сообщений НЕ разбиваем (spec-notifications §10 / Q1-А5).
    """
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    tail_reserve = 40  # запас под хвост-строку
    for i, line in enumerate(lines):
        add = len(line) + 1
        if used + add > limit - tail_reserve:
            remaining = len(lines) - i
            kept.append(f"…и ещё {remaining} строк")
            break
        kept.append(line)
        used += add
    return "\n".join(kept)
