"""REST Mini App контекста Lists (ТЗ §10) — транспортный шов.

Контекст НЕ импортирует delivery: аутентификацию (current_viewer), сессию и
общий error-middleware даёт миниапа (delivery). Хендлеры читают request['viewer']
/ request['session'], зовут ListService, сериализуют DTO; доменные ошибки (ListError /
kernel VisibilityError) ловит общий middleware миниапы.
"""

import json
from datetime import date

from aiohttp import web

from app.kernel import Scope, VisibilityDTO, VisibilityError

from app.contexts.lists.models import ItemStatus, ListType
from app.contexts.lists.services import InvalidInput, ListService


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


def _service(request: web.Request) -> ListService:
    return ListService(request["session"])


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


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidInput(f"Некорректная дата в «{field}» (ожидается YYYY-MM-DD)")


# ── DTO ──────────────────────────────────────────────────────────────────────

def _list_dto(lst, counts: dict) -> dict:
    return {
        "id": lst.id,
        "type": lst.type.value,
        "name": lst.name,
        "emoji": lst.emoji,
        "scope": lst.scope.value,
        "counts": {"open": counts.get("open", 0), "done": counts.get("done", 0)},
        "created_at": lst.created_at.isoformat() if lst.created_at else None,
    }


def _item_dto(item, hidden_from: list[int]) -> dict:
    return {
        "id": item.id,
        "list_id": item.list_id,
        "parent_id": item.parent_id,
        "text": item.text,
        "status": item.status.value,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "specific_attrs": item.specific_attrs,
        "hidden_from": hidden_from,
        "position": item.position,
        "added_by": item.added_by,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _history_dto(h) -> dict:
    return {
        "action": h.action.value,
        "detail": h.detail,
        "user_id": h.user_id,
        "at": h.at.isoformat() if h.at else None,
    }


async def _item_dto_full(svc: ListService, item) -> dict:
    return _item_dto(item, await svc.hidden_from_ids(item.id))


# ── Списки ───────────────────────────────────────────────────────────────────

async def get_lists(request: web.Request) -> web.Response:
    svc = _service(request)
    type_filter = None
    raw_type = request.query.get("type")
    if raw_type:
        type_filter = _enum(ListType, raw_type, "type")
    rows = await svc.list_lists(request["viewer"], type_filter)
    return _json([_list_dto(lst, counts) for lst, counts in rows])


async def post_list(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    name = body.get("name")
    if not name or not str(name).strip():
        raise InvalidInput("Не указано имя списка")
    list_type = _enum(ListType, body.get("type"), "type")
    scope = _enum(Scope, body.get("scope", Scope.FAMILY.value), "scope")
    lst = await svc.create_list(
        request["viewer"],
        type=list_type,
        name=name,
        emoji=body.get("emoji"),
        visibility=VisibilityDTO(scope=scope),
    )
    return _json(_list_dto(lst, {"open": 0, "done": 0}), status=201)


async def get_list(request: web.Request) -> web.Response:
    svc = _service(request)
    lst, items = await svc.get_list_detail(request["viewer"], _path_int(request, "list_id"))
    counts = (await svc.lists.visible_counts(request["viewer"])).get(
        lst.id, {"open": 0, "done": 0}
    )
    dto = _list_dto(lst, counts)
    dto["items"] = [await _item_dto_full(svc, i) for i in items]
    return _json(dto)


async def patch_list(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    kwargs: dict = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "emoji" in body:
        kwargs["emoji"] = body["emoji"]
    if "scope" in body:
        kwargs["scope"] = _enum(Scope, body["scope"], "scope")
    lst = await svc.update_list(request["viewer"], _path_int(request, "list_id"), **kwargs)
    counts = (await svc.lists.visible_counts(request["viewer"])).get(
        lst.id, {"open": 0, "done": 0}
    )
    return _json(_list_dto(lst, counts))


async def delete_list(request: web.Request) -> web.Response:
    await _service(request).delete_list(request["viewer"], _path_int(request, "list_id"))
    return web.Response(status=204)


async def post_clear_done(request: web.Request) -> web.Response:
    deleted = await _service(request).clear_done(
        request["viewer"], _path_int(request, "list_id")
    )
    return _json({"deleted": deleted})


# ── Элементы ─────────────────────────────────────────────────────────────────

async def post_item(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    text = body.get("text")
    if not text or not str(text).strip():
        raise InvalidInput("Пустой текст элемента")
    item = await svc.add_item(
        request["viewer"],
        _path_int(request, "list_id"),
        text=text,
        parent_id=body.get("parent_id"),
        specific_attrs=body.get("specific_attrs"),
        hidden_from=body.get("hidden_from"),
    )
    return _json(await _item_dto_full(svc, item), status=201)


async def patch_item(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    kwargs: dict = {}
    if "text" in body:
        kwargs["text"] = body["text"]
    if "status" in body:
        kwargs["status"] = _enum(ItemStatus, body["status"], "status")
    if "specific_attrs" in body:
        kwargs["specific_attrs"] = body["specific_attrs"]
    if "hidden_from" in body:
        kwargs["hidden_from"] = body["hidden_from"]
    if "parent_id" in body:
        kwargs["parent_id"] = body["parent_id"]
    item = await svc.update_item(request["viewer"], _path_int(request, "item_id"), **kwargs)
    return _json(await _item_dto_full(svc, item))


async def delete_item(request: web.Request) -> web.Response:
    await _service(request).delete_item(request["viewer"], _path_int(request, "item_id"))
    return web.Response(status=204)


async def get_item_history(request: web.Request) -> web.Response:
    rows = await _service(request).item_history(
        request["viewer"], _path_int(request, "item_id")
    )
    return _json([_history_dto(h) for h in rows])


# ── Поиск / аналитика ────────────────────────────────────────────────────────

async def get_search(request: web.Request) -> web.Response:
    svc = _service(request)
    query = (request.query.get("q") or "").strip()
    if not query:
        return _json([])
    out = []
    for hit in await svc.search(request["viewer"], query):
        dto = {
            "kind": hit.kind,
            "list": {
                "id": hit.list.id,
                "type": hit.list.type.value,
                "name": hit.list.name,
            },
        }
        if hit.item is not None:
            dto["item"] = await _item_dto_full(svc, hit.item)
        out.append(dto)
    return _json(out)


async def get_analytics(request: web.Request) -> web.Response:
    svc = _service(request)
    list_type = _enum(ListType, request.query.get("type"), "type")
    date_from = _parse_date(request.query.get("from"), "from")
    date_to = _parse_date(request.query.get("to"), "to")
    data = await svc.analytics(request["viewer"], list_type, date_from, date_to)
    return _json(data)


# ── Монтаж ───────────────────────────────────────────────────────────────────

def register_routes(app: web.Application) -> None:
    """Регистрирует маршруты Lists. Статические подпути — раньше /lists/{id}."""
    app.router.add_get("/api/v1/lists/search", get_search)
    app.router.add_get("/api/v1/lists/analytics", get_analytics)
    app.router.add_get("/api/v1/lists", get_lists)
    app.router.add_post("/api/v1/lists", post_list)
    app.router.add_get("/api/v1/lists/{list_id}", get_list)
    app.router.add_patch("/api/v1/lists/{list_id}", patch_list)
    app.router.add_delete("/api/v1/lists/{list_id}", delete_list)
    app.router.add_post("/api/v1/lists/{list_id}/items", post_item)
    app.router.add_post("/api/v1/lists/{list_id}/clear-done", post_clear_done)
    app.router.add_patch("/api/v1/items/{item_id}", patch_item)
    app.router.add_delete("/api/v1/items/{item_id}", delete_item)
    app.router.add_get("/api/v1/items/{item_id}/history", get_item_history)
