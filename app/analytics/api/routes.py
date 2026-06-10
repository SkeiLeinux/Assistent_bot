"""REST Mini App Analytics (/api/v1/analytics) — ТЗ §9 — транспортный шов.

Как в contexts.*.api: хендлеры читают request['viewer']/request['session'] (резолв
зрителя, сессия, общий error-middleware — ответственность миниапы, web.py). Здесь —
только analytics-маршруты и DTO. Видимость-фильтровано (read-проекции). ru.

Эндпоинты (ТЗ §9):
  GET  /analytics/overview            ?from=&to=        -> OverviewDTO
  GET  /analytics/digest/preview                        -> DigestDTO (для зрителя)
  GET  /analytics/digest/schedules                      -> [DigestScheduleDTO]
  PUT  /analytics/digest/schedules/{cadence}            -> DigestScheduleDTO  (422)
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta

from aiohttp import web
from sqlalchemy import select

from app.db.time import utcnow_naive

from app.analytics.digest import compose_digest
from app.analytics.read_composition import build_overview
from app.analytics.schedule import Cadence, DigestSchedule, period_bounds


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(
        data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False)
    )


def _422(message: str) -> web.HTTPException:
    return web.HTTPUnprocessableEntity(
        text=json.dumps({"code": "invalid_input", "message": message},
                        ensure_ascii=False),
        content_type="application/json",
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _422(f"Некорректная дата: {value!r} (ожидается YYYY-MM-DD)")


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except (ValueError, TypeError):
        raise _422(f"Некорректное время: {value!r} (ожидается HH:MM)")


def _schedule_dto(s: DigestSchedule) -> dict:
    return {
        "enabled": s.enabled,
        "cadence": s.cadence.value,
        "weekday": s.weekday,
        "time_of_day": s.time_of_day.strftime("%H:%M"),
        "last_sent_at": s.last_sent_at.isoformat() if s.last_sent_at else None,
    }


# ── Обзор ────────────────────────────────────────────────────────────────────

async def get_overview(request) -> web.Response:
    viewer = request["viewer"]
    q = getattr(request, "query", {})
    date_to = _parse_date(q.get("to")) or date.today()
    date_from = _parse_date(q.get("from")) or (date_to - timedelta(days=29))
    if date_from > date_to:
        raise _422("«from» позже «to»")
    ov = await build_overview(request["session"], viewer, date_from, date_to)
    return _json(ov)


# ── Превью дайджеста ─────────────────────────────────────────────────────────

async def get_digest_preview(request) -> web.Response:
    """Текущая композиция дайджеста для зрителя (без отправки, ТЗ S3)."""
    viewer = request["viewer"]
    session = request["session"]

    # Окно — как у daily-расписания семьи (если есть), иначе сегодняшний день.
    df = dt = date.today()
    if viewer.family_id is not None:
        daily = (
            await session.execute(
                select(DigestSchedule).where(
                    DigestSchedule.family_id == viewer.family_id,
                    DigestSchedule.cadence == Cadence.DAILY,
                )
            )
        ).scalar_one_or_none()
        if daily is not None:
            df, dt = period_bounds(daily, utcnow_naive())

    dto = await compose_digest(session, viewer, df, dt)
    if dto is None:
        dto = {"period": {"from": df.isoformat(), "to": dt.isoformat()}, "sections": []}
    return _json(dto)


# ── Расписания ───────────────────────────────────────────────────────────────

async def get_schedules(request) -> web.Response:
    viewer = request["viewer"]
    if viewer.family_id is None:
        return _json([])
    rows = await request["session"].execute(
        select(DigestSchedule)
        .where(DigestSchedule.family_id == viewer.family_id)
        .order_by(DigestSchedule.cadence)
    )
    return _json([_schedule_dto(s) for s in rows.scalars().all()])


async def put_schedule(request) -> web.Response:
    """Задать расписание (время/weekday/вкл). Создаёт, если ещё нет."""
    viewer = request["viewer"]
    if viewer.family_id is None:
        raise _422("Нужна семья для настройки дайджеста")

    raw_cadence = request.match_info.get("cadence", "")
    try:
        cadence = Cadence(raw_cadence)
    except ValueError:
        allowed = ", ".join(c.value for c in Cadence)
        raise _422(f"Недопустимый каденс {raw_cadence!r}. Допустимо: {allowed}")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if "time_of_day" not in body:
        raise _422("Поле «time_of_day» обязательно (HH:MM)")
    tod = _parse_time(body["time_of_day"])

    weekday = body.get("weekday")
    if cadence == Cadence.WEEKLY:
        if weekday is None:
            weekday = 6  # дефолт вс
        if not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise _422("«weekday» для weekly — целое 0..6 (вс=6)")
    else:
        weekday = None

    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _422("«enabled» — булево")

    session = request["session"]
    existing = (
        await session.execute(
            select(DigestSchedule).where(
                DigestSchedule.family_id == viewer.family_id,
                DigestSchedule.cadence == cadence,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = DigestSchedule(
            family_id=viewer.family_id, cadence=cadence, last_sent_at=None,
        )
        session.add(existing)

    existing.time_of_day = tod
    existing.weekday = weekday
    existing.enabled = enabled
    await session.flush()
    return _json(_schedule_dto(existing))


# ── Монтаж (зовёт ИНТЕГРАТОР из web.py миниапы) ───────────────────────────────

def register_routes(app: web.Application) -> None:
    """Регистрирует analytics-маршруты. Статические подпути — раньше параметрических."""
    app.router.add_get("/api/v1/analytics/overview", get_overview)
    app.router.add_get("/api/v1/analytics/digest/preview", get_digest_preview)
    app.router.add_get("/api/v1/analytics/digest/schedules", get_schedules)
    app.router.add_put("/api/v1/analytics/digest/schedules/{cadence}", put_schedule)
