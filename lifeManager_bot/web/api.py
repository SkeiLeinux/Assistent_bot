"""
web/api.py — HTTP-API мини-апы "Еда" на aiohttp.

Запускается в ТОМ ЖЕ процессе и event-loop, что и бот (см. bot.py): aiogram
ходит в Telegram через polling, а aiohttp параллельно отдаёт фронт мини-апы и
JSON-API. Общая БД — та же async_session_maker.

Аутентификация: каждый запрос к /api/* должен принести заголовок
`X-Telegram-Init-Data` с initData из window.Telegram.WebApp. Мидлварь
проверяет подпись (web/auth.py), находит/создаёт пользователя и кладёт в
request вместе с открытой сессией и FoodService.

Сериализация намеренно использует короткие имена полей фронта
(name/w/p/f/c/meal/time), чтобы дизайн-прототип почти не пришлось менять.
"""

import base64
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from config import settings
from db.database import async_session_maker
from services import FoodService, UserService
from services.llm_service import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    NutritionItem,
)
from web.auth import InitDataError, parse_user, validate_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Лимит на размер тела запроса (фото в base64). Groq принимает ~4 МБ base64,
# плюс запас на JSON-обвязку.
_MAX_BODY = 6 * 1024 * 1024


# ── JSON-ответы (ensure_ascii=False — чтобы кириллица читалась) ──────────────

def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_response(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def error_response(message: str, code: str, status: int) -> web.Response:
    return json_response({"error": message, "code": code}, status=status)


# ── Утилиты ──────────────────────────────────────────────────────────────────

def _num(value, default: float = 0.0) -> float:
    """Привести значение из формы (строка/число/пусто) к float."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default

def _num_or_none(value) -> float | None:
    """Как _num, но пустое/None → None (для целей и необязательного веса)."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _full_name(tg: dict) -> str:
    name = " ".join(p for p in (tg.get("first_name"), tg.get("last_name")) if p)
    return name or (tg.get("username") or f"id{tg.get('id')}")


# ── Сериализация доменных объектов под поля фронта ───────────────────────────

def _entry(e, tz: ZoneInfo) -> dict:
    local = e.created_at.replace(tzinfo=timezone.utc).astimezone(tz)
    return {
        "id": e.id,
        "time": local.strftime("%H:%M"),
        "meal": e.meal_type,
        "name": e.description,
        "w": e.weight_g,
        "kcal": round(e.kcal, 1),
        "p": round(e.protein, 1),
        "f": round(e.fat, 1),
        "c": round(e.carbs, 1),
        "src": e.source,
    }


def _totals(t) -> dict:
    return {
        "kcal": round(t.kcal, 1),
        "p": round(t.protein, 1),
        "f": round(t.fat, 1),
        "c": round(t.carbs, 1),
    }


def _goals(g) -> dict:
    return {"kcal": g.kcal, "protein": g.protein, "fat": g.fat, "carbs": g.carbs}


def _progress(prog: dict) -> dict:
    return {
        key: {
            "consumed": round(mp.consumed, 1),
            "target": mp.target,
            "percent": mp.percent,
            "remaining": mp.remaining,
        }
        for key, mp in prog.items()
    }


def _shared_food(f, user_id: int) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "weight_g": f.weight_g,
        "kcal_per_100g": round(f.kcal_per_100g, 2),
        "protein_per_100g": round(f.protein_per_100g, 2),
        "fat_per_100g": round(f.fat_per_100g, 2),
        "carbs_per_100g": round(f.carbs_per_100g, 2),
        "use_count": f.use_count,
        "by_me": f.created_by == user_id,
    }


def _item(it: NutritionItem) -> dict:
    return {
        "name": it.description,
        "w": it.weight_g,
        "kcal": round(it.kcal, 1),
        "p": round(it.protein, 1),
        "f": round(it.fat, 1),
        "c": round(it.carbs, 1),
    }


def _items_from_body(raw_items: list[dict]) -> list[NutritionItem]:
    items: list[NutritionItem] = []
    for i in raw_items:
        name = (i.get("name") or "").strip()
        if not name:
            continue
        items.append(
            NutritionItem(
                description=name,
                weight_g=_num_or_none(i.get("w")),
                kcal=_num(i.get("kcal")),
                protein=_num(i.get("p")),
                fat=_num(i.get("f")),
                carbs=_num(i.get("c")),
            )
        )
    return items


# ── Хендлеры API ─────────────────────────────────────────────────────────────

async def get_day(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    day = _parse_date(request.query.get("date")) or food.today(user)
    tz = food._tz(user)

    view = await food.get_day(user, day)
    return json_response(
        {
            "date": day.isoformat(),
            "is_today": day == food.today(user),
            "entries": [_entry(e, tz) for e in view.entries],
            "totals": _totals(view.totals),
            "goals": _goals(view.goals),
            "progress": _progress(view.progress()),
        }
    )


async def get_goals(request: web.Request) -> web.Response:
    food: FoodService = request["food_service"]
    return json_response(_goals(food.get_goals(request["user"])))


async def put_goals(request: web.Request) -> web.Response:
    food: FoodService = request["food_service"]
    body = await request.json()
    goals = await food.set_goals(
        request["user"],
        kcal=_num_or_none(body.get("kcal")),
        protein=_num_or_none(body.get("protein")),
        fat=_num_or_none(body.get("fat")),
        carbs=_num_or_none(body.get("carbs")),
    )
    return json_response(_goals(goals))


async def analyze(request: web.Request) -> web.Response:
    food: FoodService = request["food_service"]
    body = await request.json()
    kind = body.get("type")

    if kind == "text":
        text = (body.get("text") or "").strip()
        if not text:
            return error_response("Пустой запрос", "bad_request", 400)
        result = await food.analyze_text(text)
    elif kind == "photo":
        image_b64 = body.get("image") or ""
        if image_b64.startswith("data:"):
            # data:image/jpeg;base64,XXXX → берём часть после запятой
            image_b64 = image_b64.split(",", 1)[-1]
        try:
            image_bytes = base64.b64decode(image_b64)
        except (ValueError, TypeError):
            return error_response("Не удалось прочитать фото", "bad_request", 400)
        if not image_bytes:
            return error_response("Пустое фото", "bad_request", 400)
        result = await food.analyze_photo(image_bytes, caption=body.get("caption", ""))
    else:
        return error_response("Неизвестный тип запроса", "bad_request", 400)

    return json_response({"items": [_item(it) for it in result.items]})


def _created_at_for(food: FoodService, user, day: date | None) -> datetime | None:
    """Если запись добавляют в прошлый день — ставим полдень того дня (UTC-naive)."""
    if day is None or day == food.today(user):
        return None
    tz = food._tz(user)
    local_dt = datetime.combine(day, time(12, 0), tzinfo=tz)
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


async def post_entries(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    tz = food._tz(user)
    body = await request.json()

    # Разбираем items вручную, чтобы извлечь флаг save параллельно с NutritionItem
    raw_items = body.get("items") or []
    items = []
    save_flags = []
    for ri in raw_items:
        name = (ri.get("name") or "").strip()
        if not name:
            continue
        items.append(NutritionItem(
            description=name,
            weight_g=_num_or_none(ri.get("w")),
            kcal=_num(ri.get("kcal")),
            protein=_num(ri.get("p")),
            fat=_num(ri.get("f")),
            carbs=_num(ri.get("c")),
        ))
        save_flags.append(bool(ri.get("save")))

    if not items:
        return error_response("Нет позиций для сохранения", "bad_request", 400)

    created_at = _created_at_for(food, user, _parse_date(body.get("date")))
    saved = await food.save_entries(
        user,
        items,
        source=body.get("source", "manual"),
        meal_type=body.get("meal_type"),
        created_at=created_at,
        save_to_db=save_flags,
    )
    return json_response(
        {"count": len(saved), "saved": [_entry(e, tz) for e in saved]}
    )


async def post_food(request: web.Request) -> web.Response:
    """Сохранить блюдо напрямую (после analyze-photos или вручную)."""
    user = request["user"]
    food: FoodService = request["food_service"]
    body = await request.json()

    name = (body.get("name") or "").strip()
    weight_g = _num_or_none(body.get("weight_g"))
    kcal = _num_or_none(body.get("kcal"))
    protein = _num_or_none(body.get("protein"))
    fat = _num_or_none(body.get("fat"))
    carbs = _num_or_none(body.get("carbs"))

    if not name or not weight_g or weight_g <= 0:
        return error_response("Обязательны: name и weight_g > 0", "bad_request", 400)
    if any(v is None for v in (kcal, protein, fat, carbs)):
        return error_response("Обязательны: kcal, protein, fat, carbs", "bad_request", 400)

    sf = await food.save_shared_food(
        name=name, weight_g=weight_g,
        kcal=kcal, protein=protein, fat=fat, carbs=carbs,
        user_id=user.id,
    )
    await food.session.commit()
    return json_response(_shared_food(sf, user.id), status=201)


async def post_food_analyze(request: web.Request) -> web.Response:
    """Проанализировать состав блюда через LLM, вернуть КБЖУ без сохранения.

    Режим определяется автоматически по наличию полей:
      text + images → combined (читает этикетки + применяет граммовки из текста)
      только text   → text
      только images → photos
    """
    food: FoodService = request["food_service"]
    body = await request.json()

    name = (body.get("name") or "").strip()
    weight_g = _num_or_none(body.get("weight_g"))

    if not name or not weight_g or weight_g <= 0:
        return error_response("Обязательны: name и weight_g > 0", "bad_request", 400)

    description = (body.get("text") or "").strip()
    raw_images = body.get("images") or []
    images_b64 = []
    for img in raw_images[:5]:
        b64 = img.split(",", 1)[-1] if img.startswith("data:") else img
        if b64:
            images_b64.append(b64)

    has_text = bool(description)
    has_images = bool(images_b64)

    if not has_text and not has_images:
        return error_response("Нужен текст состава или хотя бы одно фото", "bad_request", 400)

    if has_text and has_images:
        result = await food.analyze_dish_combined(description, images_b64, name, weight_g)
    elif has_text:
        result = await food.analyze_dish_text(description, name, weight_g)
    else:
        result = await food.analyze_dish_photos(images_b64, name, weight_g)

    item = result.items[0]
    return json_response({
        "kcal": round(item.kcal, 1),
        "protein": round(item.protein, 1),
        "fat": round(item.fat, 1),
        "carbs": round(item.carbs, 1),
    })


async def get_foods(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    q = request.query.get("q", "").strip()
    if q:
        items = await food.search_shared_foods(q)
    else:
        items = await food.get_quick_pick(user)
    return json_response([_shared_food(f, user.id) for f in items])


async def post_entry_from_food(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    tz = food._tz(user)
    body = await request.json()

    food_id = body.get("food_id")
    weight_g = _num_or_none(body.get("weight_g"))
    if not food_id or not weight_g or weight_g <= 0:
        return error_response("Не задан food_id или weight_g", "bad_request", 400)

    created_at = _created_at_for(food, user, _parse_date(body.get("date")))
    entry = await food.create_entry_from_shared(
        user, int(food_id), weight_g,
        meal_type=body.get("meal_type"),
        created_at=created_at,
    )
    if entry is None:
        return error_response("Блюдо не найдено", "not_found", 404)
    return json_response({"ok": True, "entry": _entry(entry, tz)})


_PATCH_FIELD_MAP = {
    "name": "description",
    "w": "weight_g",
    "kcal": "kcal",
    "p": "protein",
    "f": "fat",
    "c": "carbs",
    "meal_type": "meal_type",
}


async def patch_entry(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    tz = food._tz(user)
    entry_id = int(request.match_info["entry_id"])
    body = await request.json()

    fields: dict = {}
    for src_key, dst_key in _PATCH_FIELD_MAP.items():
        if src_key not in body:
            continue
        value = body[src_key]
        if dst_key == "description":
            fields[dst_key] = (value or "").strip()
        elif dst_key == "meal_type":
            fields[dst_key] = value
        elif dst_key == "weight_g":
            fields[dst_key] = _num_or_none(value)
        else:
            fields[dst_key] = _num(value)

    if not fields:
        return error_response("Нет полей для обновления", "bad_request", 400)

    entry = await food.update_entry(user, entry_id, **fields)
    if entry is None:
        return error_response("Запись не найдена", "not_found", 404)
    return json_response(_entry(entry, tz))


async def delete_entry(request: web.Request) -> web.Response:
    entry_id = int(request.match_info["entry_id"])
    ok = await request["food_service"].delete_entry(request["user"], entry_id)
    if not ok:
        return error_response("Запись не найдена", "not_found", 404)
    return json_response({"ok": True})


async def get_period(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]

    end = _parse_date(request.query.get("to")) or food.today(user)
    start = _parse_date(request.query.get("from")) or (end - timedelta(days=6))

    period = await food.get_period(user, start, end)
    days = [
        {
            "date": d.day.isoformat(),
            "totals": _totals(d.totals),
            "count": len(d.entries),
        }
        for d in period.days
    ]
    return json_response(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "today": food.today(user).isoformat(),
            "goal_kcal": user.target_kcal,
            "totals": _totals(period.totals),
            "days": days,
        }
    )


async def get_export(request: web.Request) -> web.Response:
    user = request["user"]
    food: FoodService = request["food_service"]
    end = _parse_date(request.query.get("to")) or food.today(user)
    start = _parse_date(request.query.get("from")) or (end - timedelta(days=6))
    markdown = await food.export_period(user, start, end)
    return json_response({"markdown": markdown})


# ── Мидлвари ─────────────────────────────────────────────────────────────────

@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Аутентификация для /api/*. Статика и index отдаются без проверки."""
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        data = validate_init_data(init_data, settings.bot_token)
        tg = parse_user(data)
    except InitDataError as exc:
        return error_response(f"Авторизация не прошла: {exc}", "auth", 401)

    tg_id = tg.get("id")
    if settings.allowed_user_ids and tg_id not in settings.allowed_user_ids:
        return error_response("Нет доступа к боту", "forbidden", 403)

    async with async_session_maker() as session:
        user = await UserService(session).get_or_create_user(
            telegram_id=tg_id,
            full_name=_full_name(tg),
            username=tg.get("username"),
        )
        request["user"] = user
        request["session"] = session
        request["food_service"] = FoodService(session)
        return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Ловит доменные ошибки LLM и непойманные исключения → аккуратный JSON."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except LLMNotConfiguredError as exc:
        return error_response(str(exc), "no_key", 503)
    except LLMRateLimitError as exc:
        return error_response(str(exc), "rate", 429)
    except LLMError as exc:
        return error_response(str(exc), "no_llm", 502)
    except json.JSONDecodeError:
        return error_response("Некорректный JSON в запросе", "bad_request", 400)
    except Exception as exc:  # noqa: BLE001 — последний рубеж, чтобы не отдать HTML-500
        logger.exception("Необработанная ошибка в API: %s", exc)
        return error_response("Внутренняя ошибка сервера", "server", 500)


# ── Статика ──────────────────────────────────────────────────────────────────

async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


# ── Сборка приложения ────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, auth_middleware],
        client_max_size=_MAX_BODY,
    )

    app.router.add_get("/api/day", get_day)
    app.router.add_get("/api/goals", get_goals)
    app.router.add_put("/api/goals", put_goals)
    app.router.add_post("/api/analyze", analyze)
    app.router.add_post("/api/entries", post_entries)
    app.router.add_post("/api/entries/from-food", post_entry_from_food)
    app.router.add_patch("/api/entries/{entry_id}", patch_entry)
    app.router.add_delete("/api/entries/{entry_id}", delete_entry)
    app.router.add_get("/api/period", get_period)
    app.router.add_get("/api/export", get_export)
    app.router.add_get("/api/foods", get_foods)
    app.router.add_post("/api/foods", post_food)
    app.router.add_post("/api/foods/analyze", post_food_analyze)

    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start_web_server() -> web.AppRunner:
    """Поднять aiohttp в текущем event-loop. Возвращает runner для cleanup."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    logger.info(
        "Веб-сервер мини-апы слушает http://%s:%s",
        settings.webapp_host,
        settings.webapp_port,
    )
    return runner
