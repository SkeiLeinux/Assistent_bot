"""REST Mini App контекста Reminders (ТЗ §9) — транспортный шов.

Контекст НЕ импортирует delivery: аутентификацию (current_viewer), сессию и общий
error-middleware даёт миниапа (delivery). Хендлеры читают request['viewer'] /
request['session'], зовут ReminderService, сериализуют DTO; доменные ошибки
(ReminderError / kernel) ловит общий middleware миниапы.

ack/snooze доступны и из REST (здесь), и из callback push-кнопок (бот → тот же
ReminderService) — §9.
"""

import json
from datetime import datetime

from aiohttp import web

from app.kernel import Scope

from app.contexts.reminders.models import Recurrence, ReminderStatus
from app.contexts.reminders.services import InvalidInput, ReminderService


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(
        data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False)
    )


async def _body(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _service(request: web.Request) -> ReminderService:
    return ReminderService(request["session"])


def _path_int(request: web.Request, key: str) -> int:
    try:
        return int(request.match_info[key])
    except (KeyError, ValueError):
        raise InvalidInput("Некорректный идентификатор в пути")


def _enum(enum_cls, value, field: str):
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise InvalidInput(f"Недопустимое значение «{field}». Допустимо: {allowed}")


def _parse_dt(value, field: str) -> datetime:
    if not value:
        raise InvalidInput(f"Не указано «{field}»")
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise InvalidInput(f"Некорректное время в «{field}» (ISO 8601)")


# ── DTO ────────────────────────────────────────────────────────────────────────

def _dto(rem) -> dict:
    return {
        "id": rem.id,
        "text": rem.text,
        "next_fire_at": rem.next_fire_at.isoformat() if rem.next_fire_at else None,
        "recurrence": rem.recurrence.value,
        "interval_seconds": rem.interval_seconds,
        "scope": rem.scope.value,
        "status": rem.status.value,
        "last_fired_at": rem.last_fired_at.isoformat() if rem.last_fired_at else None,
        "owner_id": rem.owner_id,
        "created_at": rem.created_at.isoformat() if rem.created_at else None,
    }


# ── Хендлеры ─────────────────────────────────────────────────────────────────

async def get_reminders(request: web.Request) -> web.Response:
    svc = _service(request)
    status = None
    raw_status = request.query.get("status")
    if raw_status:
        status = _enum(ReminderStatus, raw_status, "status")
    rows = await svc.list_reminders(request["viewer"], status)
    return _json([_dto(r) for r in rows])


async def post_reminder(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    text = body.get("text")
    if not text or not str(text).strip():
        raise InvalidInput("Не указан текст напоминания")
    fire_at = _parse_dt(body.get("fire_at"), "fire_at")
    recurrence = _enum(Recurrence, body.get("recurrence", Recurrence.NONE.value), "recurrence")
    scope = _enum(Scope, body.get("scope", Scope.PERSONAL.value), "scope")
    interval_seconds = body.get("interval_seconds")
    rem = await svc.create_reminder(
        request["viewer"],
        text=text,
        fire_at=fire_at,
        recurrence=recurrence,
        interval_seconds=interval_seconds,
        scope=scope,
    )
    return _json(_dto(rem), status=201)


async def get_reminder(request: web.Request) -> web.Response:
    rem = await _service(request).get_reminder(
        request["viewer"], _path_int(request, "reminder_id")
    )
    return _json(_dto(rem))


async def patch_reminder(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    kwargs: dict = {}
    if "text" in body:
        kwargs["text"] = body["text"]
    if "fire_at" in body:
        kwargs["fire_at"] = _parse_dt(body["fire_at"], "fire_at")
    if "recurrence" in body:
        kwargs["recurrence"] = _enum(Recurrence, body["recurrence"], "recurrence")
    if "interval_seconds" in body:
        kwargs["interval_seconds"] = body["interval_seconds"]
    if "scope" in body:
        kwargs["scope"] = _enum(Scope, body["scope"], "scope")
    rem = await svc.update_reminder(
        request["viewer"], _path_int(request, "reminder_id"), **kwargs
    )
    return _json(_dto(rem))


async def delete_reminder(request: web.Request) -> web.Response:
    await _service(request).delete_reminder(
        request["viewer"], _path_int(request, "reminder_id")
    )
    return web.Response(status=204)


async def post_ack(request: web.Request) -> web.Response:
    rem = await _service(request).ack(
        request["viewer"], _path_int(request, "reminder_id")
    )
    return _json(_dto(rem))


async def post_snooze(request: web.Request) -> web.Response:
    body = await _body(request)
    minutes = body.get("minutes")
    if not isinstance(minutes, int):
        raise InvalidInput("Не указано «minutes» (целое число минут)")
    rem = await _service(request).snooze(
        request["viewer"], _path_int(request, "reminder_id"), minutes
    )
    return _json(_dto(rem))


# ── Монтаж ─────────────────────────────────────────────────────────────────────

def register_routes(app: web.Application) -> None:
    """Регистрирует маршруты Reminders под /api/v1/reminders (§9)."""
    app.router.add_get("/api/v1/reminders", get_reminders)
    app.router.add_post("/api/v1/reminders", post_reminder)
    app.router.add_get("/api/v1/reminders/{reminder_id}", get_reminder)
    app.router.add_patch("/api/v1/reminders/{reminder_id}", patch_reminder)
    app.router.add_delete("/api/v1/reminders/{reminder_id}", delete_reminder)
    app.router.add_post("/api/v1/reminders/{reminder_id}/ack", post_ack)
    app.router.add_post("/api/v1/reminders/{reminder_id}/snooze", post_snooze)
