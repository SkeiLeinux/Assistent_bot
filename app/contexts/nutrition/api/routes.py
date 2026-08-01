"""REST Mini App контекста Nutrition (ТЗ §9/§10) — транспортный шов.

Контекст НЕ импортирует delivery: аутентификацию (current_viewer), сессию и общий
error-middleware даёт миниапа (delivery). Хендлеры читают request['viewer']
(видимость) и request['user'] (timezone для границ суток), зовут FoodService,
сериализуют под короткие имена полей фронта (name/w/p/f/c/meal/time) — чтобы
дизайн-прототип почти не пришлось менять. Доменные/LLM-ошибки ловит общий
middleware миниапы.

Пути — под префиксом `/api/v1/nutrition/*` (дельта OQ-N3); поведение 1:1 с
current-state (маппинг старых bare-путей — ТЗ §9).
"""

import base64
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import web

from app.contexts.nutrition.resolve import build_extractor, build_resolver
from app.contexts.nutrition.services import FoodService, InvalidInput, NutritionItem

_DEFAULT_TZ = "Europe/Amsterdam"

# Провенанс КБЖУ (ADR-0013, ProvenanceSource) — допустимые значения source.
# Канал ввода ('text'/'photo') больше не провенанс → нормализуем в 'manual'.
_VALID_PROVENANCE = {
    "label", "off", "web", "nutritionix", "estimate", "db", "manual",
}


def _provenance(value) -> str | None:
    """Валидный ProvenanceSource или None (неизвестное/канал ввода → None)."""
    return value if value in _VALID_PROVENANCE else None


# ── JSON / разбор ────────────────────────────────────────────────────────────

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


def _service(request: web.Request) -> FoodService:
    """Композиционный корень: собираем извлекатель+резолвер (ADR-0013) и инжектим.

    Здесь, в api-слое, допустимо тянуть resolve (FoodService — нет, границы §2).
    Конструкция дешёвая (сеть к OFF — только на самом lookup); резолвер деградирует
    в estimate, если источник недоступен.
    """
    return FoodService(
        request["session"],
        extractor=build_extractor(),
        resolver=build_resolver(),
    )


def _viewer(request: web.Request):
    return request["viewer"]


def _tz_name(request: web.Request) -> str:
    """Таймзона пользователя из kernel-User (границы суток). Дефолт безопасен."""
    user = request.get("user")
    return getattr(user, "timezone", None) or _DEFAULT_TZ


def _tz(request: web.Request) -> ZoneInfo:
    try:
        return ZoneInfo(_tz_name(request))
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _num(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _num_or_none(value) -> float | None:
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


def _path_int(request: web.Request, key: str) -> int:
    try:
        return int(request.match_info[key])
    except (KeyError, ValueError):
        raise InvalidInput("Некорректный идентификатор в пути")


# ── Сериализация под поля фронта ─────────────────────────────────────────────

def _ingredient(di) -> dict:
    """Строка состава составного блюда (абсолют за порцию ингредиента + провенанс)."""
    return {
        "name": di.name,
        "w": di.weight_g,
        "kcal": round(di.kcal, 1),
        "p": round(di.protein, 1),
        "f": round(di.fat, 1),
        "c": round(di.carbs, 1),
        "source": di.source,
        "source_ref": di.source_ref,
    }


def _entry(e, tz: ZoneInfo, ingredients=None) -> dict:
    local = e.created_at.replace(tzinfo=timezone.utc).astimezone(tz)
    out = {
        "id": e.id,
        "time": local.strftime("%H:%M"),
        "meal": e.meal_type,
        "name": e.description,
        "w": e.weight_g,
        "kcal": round(e.kcal, 1),
        "p": round(e.protein, 1),
        "f": round(e.fat, 1),
        "c": round(e.carbs, 1),
        "src": e.source,             # провенанс (ADR-0013)
        "source_ref": e.source_ref,  # ссылка/код источника (off/web)
    }
    # Состав составного блюда (фича 2026-06-14): фронт показывает «из чего состоит».
    if ingredients:
        out["ingredients"] = [_ingredient(di) for di in ingredients]
    return out


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


def _shared_food(f, viewer_id: int, ingredients=None) -> dict:
    out = {
        "id": f.id,
        "name": f.name,
        "weight_g": f.weight_g,
        "kcal_per_100g": round(f.kcal_per_100g, 2),
        "protein_per_100g": round(f.protein_per_100g, 2),
        "fat_per_100g": round(f.fat_per_100g, 2),
        "carbs_per_100g": round(f.carbs_per_100g, 2),
        "use_count": f.use_count,
        "by_me": f.created_by == viewer_id,
    }
    # Состав блюда каталога (фича 2026-06-14): «посмотреть из чего состоит».
    if ingredients:
        out["ingredients"] = [_ingredient(di) for di in ingredients]
    return out


def _item(it: NutritionItem) -> dict:
    # Провенанс на позицию (ADR-0013): фронт показывает источник, save несёт его обратно.
    out = {
        "name": it.description,
        "w": it.weight_g,
        "kcal": round(it.kcal, 1),
        "p": round(it.protein, 1),
        "f": round(it.fat, 1),
        "c": round(it.carbs, 1),
        "source": it.source,
        "source_ref": it.source_ref,
        "matched_name": it.matched_name,
    }
    # Флаги проверок §6 — только когда есть: фронт рисует текст по коду.
    if it.warnings:
        out["warnings"] = it.warnings
    # per100g (опция C): confirm-экран пересчитывает КБЖУ при правке веса.
    if it.kcal_100g is not None:
        out["per100g"] = {
            "kcal": it.kcal_100g,
            "p": it.protein_100g,
            "f": it.fat_100g,
            "c": it.carbs_100g,
        }
    return out


def _created_at_for(
    svc: FoodService, tz_name: str, today: date, day: date | None
) -> datetime | None:
    """Запись в прошлый день → полдень того дня (UTC-naive). Сегодня → None."""
    if day is None or day == today:
        return None
    tz = svc._tz(tz_name)
    local_dt = datetime.combine(day, time(12, 0), tzinfo=tz)
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


# ── День / цели ──────────────────────────────────────────────────────────────

async def get_day(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    tz = _tz(request)
    today = svc.today(tz_name)
    day = _parse_date(request.query.get("date")) or today

    view = await svc.get_day(viewer, day, timezone_name=tz_name)
    # Состав составных блюд — одним батч-запросом (без N+1), кладём на записи.
    ing_map = await svc.ingredients_for_entries([e.id for e in view.entries])
    return _json(
        {
            "date": day.isoformat(),
            "is_today": day == today,
            "entries": [_entry(e, tz, ing_map.get(e.id)) for e in view.entries],
            "totals": _totals(view.totals),
            "goals": _goals(view.goals),
            "progress": _progress(view.progress()),
        }
    )


async def get_goals(request: web.Request) -> web.Response:
    svc = _service(request)
    return _json(_goals(await svc.get_goals(_viewer(request))))


async def put_goals(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    goals = await svc.set_goals(
        _viewer(request),
        kcal=_num_or_none(body.get("kcal")),
        protein=_num_or_none(body.get("protein")),
        fat=_num_or_none(body.get("fat")),
        carbs=_num_or_none(body.get("carbs")),
    )
    return _json(_goals(goals))


# ── Анализ (LLM, без записи, I5) ─────────────────────────────────────────────

async def analyze(request: web.Request) -> web.Response:
    svc = _service(request)
    body = await _body(request)
    kind = body.get("type")

    if kind == "text":
        text = (body.get("text") or "").strip()
        if not text:
            raise InvalidInput("Пустой запрос")
        result = await svc.analyze_text(text)
    elif kind == "photo":
        # До 5 снимков (паритет с каталогом). Поддерживаем и одиночное поле `image`
        # (обратная совместимость), и список `images` (dataURL или «голый» base64).
        raw_images = body.get("images")
        if raw_images is None:
            single = body.get("image")
            raw_images = [single] if single else []
        images: list[bytes] = []
        for img in (raw_images or [])[:5]:
            if not isinstance(img, str):
                continue
            b64 = img.split(",", 1)[-1] if img.startswith("data:") else img
            if not b64:
                continue
            try:
                decoded = base64.b64decode(b64)
            except (ValueError, TypeError):
                raise InvalidInput("Не удалось прочитать фото")
            if decoded:
                images.append(decoded)
        if not images:
            raise InvalidInput("Пустое фото")
        result = await svc.analyze_photo(images, caption=body.get("caption", ""))
    else:
        raise InvalidInput("Неизвестный тип запроса")

    # `unresolved` (ТЗ §6.6): позиции без чисел не блокируют остальные и НЕ
    # приезжают нулями в items — запись с нулевым КБЖУ выглядит как данные и
    # портит итоги дня, прогресс к целям и дайджест. Пустой items при непустом
    # unresolved — это 200 (частичный успех), а не 502.
    return _json(
        {
            "items": [_item(it) for it in result.items],
            "unresolved": [
                {"name": u.name, "note": u.note, "reason": u.reason}
                for u in result.unresolved
            ],
        }
    )


# ── Записи дневника ──────────────────────────────────────────────────────────

async def post_entries(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    tz = _tz(request)
    body = await _body(request)

    raw_items = body.get("items") or []
    items: list[NutritionItem] = []
    save_flags: list[bool] = []
    for ri in raw_items:
        name = (ri.get("name") or "").strip()
        if not name:
            continue
        items.append(
            NutritionItem(
                description=name,
                weight_g=_num_or_none(ri.get("w")),
                kcal=_num(ri.get("kcal")),
                protein=_num(ri.get("p")),
                fat=_num(ri.get("f")),
                carbs=_num(ri.get("c")),
                # Провенанс позиции (ADR-0013): из /analyze; ручная позиция → None.
                source=_provenance(ri.get("source")),
                source_ref=(ri.get("source_ref") or None),
            )
        )
        save_flags.append(bool(ri.get("save")))

    if not items:
        raise InvalidInput("Нет позиций для сохранения")

    today = svc.today(tz_name)
    created_at = _created_at_for(svc, tz_name, today, _parse_date(body.get("date")))
    saved = await svc.save_entries(
        viewer,
        items,
        # Дефолт для позиций без своего провенанса; канал ввода → 'manual'.
        source=_provenance(body.get("source")) or "manual",
        timezone_name=tz_name,
        meal_type=body.get("meal_type"),
        created_at=created_at,
        save_to_db=save_flags,
    )
    return _json({"count": len(saved), "saved": [_entry(e, tz) for e in saved]})


async def post_entry_composite(request: web.Request) -> web.Response:
    """Свернуть распознанные позиции в ОДНО блюдо: запись дневника + состав-снимок.

    Тело: {name, ingredients:[{name,w,kcal,p,f,c,source?,source_ref?}], meal_type?,
    date?, source?, save_to_catalog?}. Запись создаётся всегда; при save_to_catalog —
    ещё и блюдо каталога со своим составом (требует семьи). Возвращает запись с составом.
    """
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    tz = _tz(request)
    body = await _body(request)

    name = (body.get("name") or "").strip()
    if not name:
        raise InvalidInput("Не задано название блюда")

    ingredients: list[dict] = []
    for ri in body.get("ingredients") or []:
        ing_name = (ri.get("name") or "").strip()
        if not ing_name:
            continue
        ingredients.append(
            {
                "name": ing_name,
                "weight_g": _num_or_none(ri.get("w")),
                "kcal": _num(ri.get("kcal")),
                "protein": _num(ri.get("p")),
                "fat": _num(ri.get("f")),
                "carbs": _num(ri.get("c")),
                # Провенанс позиции из /analyze (ADR-0013); ручная → None.
                "source": _provenance(ri.get("source")),
                "source_ref": (ri.get("source_ref") or None),
            }
        )
    if not ingredients:
        raise InvalidInput("Пустой состав блюда")

    today = svc.today(tz_name)
    created_at = _created_at_for(svc, tz_name, today, _parse_date(body.get("date")))
    entry = await svc.save_composite_dish(
        viewer,
        name=name,
        ingredients=ingredients,
        # Дефолт провенанса записи; канал ввода ('photo'/'text') → 'manual'.
        source=_provenance(body.get("source")) or "manual",
        timezone_name=tz_name,
        meal_type=body.get("meal_type"),
        created_at=created_at,
        save_to_catalog=bool(body.get("save_to_catalog")),
    )
    ing_map = await svc.ingredients_for_entries([entry.id])
    return _json({"ok": True, "entry": _entry(entry, tz, ing_map.get(entry.id))})


async def post_entry_from_food(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    tz = _tz(request)
    body = await _body(request)

    food_id = body.get("food_id")
    weight_g = _num_or_none(body.get("weight_g"))
    if not food_id or not weight_g or weight_g <= 0:
        raise InvalidInput("Не задан food_id или weight_g")

    today = svc.today(tz_name)
    created_at = _created_at_for(svc, tz_name, today, _parse_date(body.get("date")))
    entry = await svc.create_entry_from_shared(
        viewer,
        int(food_id),
        weight_g,
        meal_type=body.get("meal_type"),
        timezone_name=tz_name,
        created_at=created_at,
    )
    if entry is None:
        return _json({"error": "Блюдо не найдено", "code": "not_found"}, status=404)
    return _json({"ok": True, "entry": _entry(entry, tz)})


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
    svc = _service(request)
    viewer = _viewer(request)
    tz = _tz(request)
    entry_id = _path_int(request, "entry_id")
    body = await _body(request)

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
        raise InvalidInput("Нет полей для обновления")

    entry = await svc.update_entry(viewer, entry_id, **fields)
    if entry is None:
        return _json({"error": "Запись не найдена", "code": "not_found"}, status=404)
    return _json(_entry(entry, tz))


async def delete_entry(request: web.Request) -> web.Response:
    ok = await _service(request).delete_entry(
        _viewer(request), _path_int(request, "entry_id")
    )
    if not ok:
        return _json({"error": "Запись не найдена", "code": "not_found"}, status=404)
    return _json({"ok": True})


# ── Период / выгрузка ────────────────────────────────────────────────────────

async def get_period(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    today = svc.today(tz_name)

    end = _parse_date(request.query.get("to")) or today
    start = _parse_date(request.query.get("from")) or (end - timedelta(days=6))

    period = await svc.get_period(viewer, start, end, timezone_name=tz_name)
    goals = await svc.get_goals(viewer)
    days = [
        {
            "date": d.day.isoformat(),
            "totals": _totals(d.totals),
            "count": len(d.entries),
        }
        for d in period.days
    ]
    return _json(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "today": today.isoformat(),
            "goal_kcal": goals.kcal,
            "totals": _totals(period.totals),
            "days": days,
        }
    )


async def get_export(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    tz_name = _tz_name(request)
    today = svc.today(tz_name)
    end = _parse_date(request.query.get("to")) or today
    start = _parse_date(request.query.get("from")) or (end - timedelta(days=6))
    markdown = await svc.export_period(viewer, start, end, timezone_name=tz_name)
    return _json({"markdown": markdown})


# ── Каталог блюд (FAMILY) ────────────────────────────────────────────────────

async def get_foods(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    q = request.query.get("q", "").strip()
    if q:
        items = await svc.search_shared_foods(viewer, q)
    else:
        items = await svc.get_quick_pick(viewer)
    # Состав блюд каталога — батчем (без N+1).
    ing_map = await svc.ingredients_for_foods([f.id for f in items])
    return _json([_shared_food(f, viewer.id, ing_map.get(f.id)) for f in items])


async def post_food(request: web.Request) -> web.Response:
    svc = _service(request)
    viewer = _viewer(request)
    body = await _body(request)

    name = (body.get("name") or "").strip()
    weight_g = _num_or_none(body.get("weight_g"))
    kcal = _num_or_none(body.get("kcal"))
    protein = _num_or_none(body.get("protein"))
    fat = _num_or_none(body.get("fat"))
    carbs = _num_or_none(body.get("carbs"))

    if not name or not weight_g or weight_g <= 0:
        raise InvalidInput("Обязательны: name и weight_g > 0")
    if any(v is None for v in (kcal, protein, fat, carbs)):
        raise InvalidInput("Обязательны: kcal, protein, fat, carbs")

    sf = await svc.save_shared_food(
        viewer,
        name=name,
        weight_g=weight_g,
        kcal=kcal,
        protein=protein,
        fat=fat,
        carbs=carbs,
    )
    await svc.session.commit()
    return _json(_shared_food(sf, viewer.id), status=201)


async def post_food_analyze(request: web.Request) -> web.Response:
    """LLM-разбор состава блюда → КБЖУ без сохранения. Режим — по наличию полей.

    text + images → combined; только text → text; только images → photos.
    """
    svc = _service(request)
    body = await _body(request)

    name = (body.get("name") or "").strip()
    weight_g = _num_or_none(body.get("weight_g"))
    if not name or not weight_g or weight_g <= 0:
        raise InvalidInput("Обязательны: name и weight_g > 0")

    description = (body.get("text") or "").strip()
    raw_images = body.get("images") or []
    images_b64: list[str] = []
    for img in raw_images[:5]:
        b64 = img.split(",", 1)[-1] if img.startswith("data:") else img
        if b64:
            images_b64.append(b64)

    has_text = bool(description)
    has_images = bool(images_b64)
    if not has_text and not has_images:
        raise InvalidInput("Нужен текст состава или хотя бы одно фото")

    if has_text and has_images:
        result = await svc.analyze_dish_combined(description, images_b64, name, weight_g)
    elif has_text:
        result = await svc.analyze_dish_text(description, name, weight_g)
    else:
        result = await svc.analyze_dish_photos(images_b64, name, weight_g)

    if not result.items:
        raise InvalidInput("Не удалось разобрать состав блюда")

    # items[0] — итог, посчитанный КОДОМ (L2, ТЗ §5.4); дальше идут позиции,
    # не вошедшие в него (без граммовки). Провенанс блюда всегда estimate:
    # каталог — расчётный путь (ADR-0013 §17), резолвер не вызывается.
    item = result.items[0]
    out = {
        "kcal": round(item.kcal, 1),
        "protein": round(item.protein, 1),
        "fat": round(item.fat, 1),
        "carbs": round(item.carbs, 1),
        "source": "estimate",
        "source_ref": None,
    }
    if item.warnings:
        out["warnings"] = item.warnings
    return _json(out)


# ── Монтаж ───────────────────────────────────────────────────────────────────

def register_routes(app: web.Application) -> None:
    """Регистрирует маршруты Nutrition под /api/v1/nutrition/*.

    Статические подпути (/foods, /entries/from-food) — раньше параметрических
    (/entries/{id}), чтобы не перехватывались.
    """
    app.router.add_get("/api/v1/nutrition/day", get_day)
    app.router.add_get("/api/v1/nutrition/goals", get_goals)
    app.router.add_put("/api/v1/nutrition/goals", put_goals)
    app.router.add_post("/api/v1/nutrition/analyze", analyze)
    app.router.add_post("/api/v1/nutrition/entries/from-food", post_entry_from_food)
    app.router.add_post("/api/v1/nutrition/entries/composite", post_entry_composite)
    app.router.add_post("/api/v1/nutrition/entries", post_entries)
    app.router.add_patch("/api/v1/nutrition/entries/{entry_id}", patch_entry)
    app.router.add_delete("/api/v1/nutrition/entries/{entry_id}", delete_entry)
    app.router.add_get("/api/v1/nutrition/period", get_period)
    app.router.add_get("/api/v1/nutrition/export", get_export)
    app.router.add_get("/api/v1/nutrition/foods", get_foods)
    app.router.add_post("/api/v1/nutrition/foods/analyze", post_food_analyze)
    app.router.add_post("/api/v1/nutrition/foods", post_food)
