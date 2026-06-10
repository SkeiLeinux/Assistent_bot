"""REST Mini App контекста Finance (ТЗ §10) — транспортный шов.

Контекст НЕ импортирует delivery: аутентификацию (current_viewer), сессию и общий
error-middleware даёт миниапа. Хендлеры читают request['viewer']/request['session'],
зовут сервисы, сериализуют DTO; доменные ошибки (FinanceError / ImportStructureError /
kernel VisibilityError) ловит общий middleware миниапы. Данные — FAMILY (family_id).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from aiohttp import web

from app.contexts.finance.importing import ImportStructureError
from app.contexts.finance.models import CategoryStatus
from app.contexts.finance.services import (
    AnalyticsService,
    ImportService,
    InvalidInput,
    TaxonomyService,
    resolve_period,
)


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(
        data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False)
    )


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _path_int(request: web.Request, key: str) -> int:
    try:
        return int(request.match_info[key])
    except (KeyError, ValueError):
        raise InvalidInput("Некорректный идентификатор в пути")


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidInput(f"Некорректная дата в «{field}» (ожидается YYYY-MM-DD)")


def _parse_int(value: str | None, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise InvalidInput(f"Некорректное целое в «{field}»")


# ── DTO ────────────────────────────────────────────────────────────────────────

def _category_node_dto(cat, children: list) -> dict:
    return {
        "id": cat.id,
        "name": cat.name,
        "parent_id": cat.parent_id,
        "status": cat.status.value,
        "children": children,
    }


def _build_category_tree(cats: list) -> list[dict]:
    by_parent: dict[int | None, list] = {}
    for c in cats:
        by_parent.setdefault(c.parent_id, []).append(c)

    def node(cat) -> dict:
        kids = [node(c) for c in by_parent.get(cat.id, [])]
        return _category_node_dto(cat, kids)

    return [node(c) for c in by_parent.get(None, [])]


def _analytics_line_dto(line) -> dict:
    return {
        "category_id": line.category_id,
        "name": line.name,
        "plan": _num(line.plan),
        "fact": _num(line.fact),
        "diff": _num(line.diff),
        "pct": line.pct,
        "children": [_analytics_line_dto(c) for c in line.children],
    }


def _analytics_dto(result) -> dict:
    return {
        "period": result.period,
        "range": {
            "from": result.date_from.isoformat(),
            "to": result.date_to.isoformat(),
        },
        "plan_applicable": result.plan_applicable,
        "total": {
            "plan": _num(result.total_plan),
            "fact": _num(result.total_fact),
            "diff": _num(result.total_diff),
        },
        "lines": [_analytics_line_dto(l) for l in result.lines],
    }


def _income_dto(report) -> dict:
    return {
        "range": {
            "from": report.date_from.isoformat(),
            "to": report.date_to.isoformat(),
        },
        "by_source": [
            {
                "source": s.source_name,
                "kind": s.kind,
                "partner": s.user_id,
                "plan": _num(s.plan),
                "fact": _num(s.fact),
            }
            for s in report.by_source
        ],
        "carryover": _num(report.carryover),
        "total_income": _num(report.total_income),
        "total_expense": _num(report.total_expense),
        "delta": _num(report.delta),
    }


# ── Период из query ────────────────────────────────────────────────────────────

def _period_from_query(request: web.Request):
    q = request.query
    period = q.get("period")
    month = _parse_date(q.get("month"), "month")
    date_from = _parse_date(q.get("from"), "from")
    date_to = _parse_date(q.get("to"), "to")
    quarter = _parse_int(q.get("quarter"), "quarter")
    year = _parse_int(q.get("year"), "year")
    return resolve_period(
        period,
        month=month,
        quarter=quarter,
        year=year,
        date_from=date_from,
        date_to=date_to,
    )


# ── Эндпоинты ──────────────────────────────────────────────────────────────────

async def post_import(request: web.Request) -> web.Response:
    """POST /finance/import — multipart file + year."""
    reader = await request.multipart()
    file_bytes: bytes | None = None
    year: int | None = None
    async for part in reader:
        if part.name == "file":
            file_bytes = await part.read(decode=False)
        elif part.name == "year":
            raw = (await part.text()).strip()
            year = int(raw) if raw else None
    if file_bytes is None:
        raise InvalidInput("Не приложен файл (поле «file»)")
    if year is None:
        year = date.today().year  # default текущий (Q1-А1)

    svc = ImportService(request["session"])
    try:
        outcome, skipped = await svc.import_file(request["viewer"], file_bytes, year)
    except ImportStructureError as e:
        # Fail-loud: ничего не записано (I3). 422 со списком нераспознанного.
        return _json(
            {
                "status": "failed",
                "months": [],
                "created_for_review": [],
                "errors": [
                    {"row": i.row, "reason": i.reason, "sheet": i.sheet}
                    for i in e.issues
                ],
                "message": e.message,
            },
            status=422,
        )

    return _json({
        "status": "ok",
        "months": [
            {"month": m.month.isoformat(), "categories": m.categories, "income": m.income}
            for m in outcome.months
        ],
        "created_for_review": [
            _category_node_dto(c, []) for c in outcome.created_for_review
        ],
        "errors": [{"row": i.row, "reason": i.reason, "sheet": i.sheet} for i in skipped],
    })


async def get_months(request: web.Request) -> web.Response:
    svc = ImportService(request["session"])
    months = await svc.list_months(request["viewer"])
    return _json([
        {
            "month": m.month.isoformat(),
            "imported_at": None,
            "categories": m.categories,
            "has_facts": m.has_facts,
        }
        for m in months
    ])


async def get_categories(request: web.Request) -> web.Response:
    svc = TaxonomyService(request["session"])
    status = None
    raw = request.query.get("status")
    if raw:
        try:
            status = CategoryStatus(raw)
        except ValueError:
            allowed = ", ".join(m.value for m in CategoryStatus)
            raise InvalidInput(f"Недопустимый status. Допустимо: {allowed}")
    cats = await svc.list_categories(request["viewer"], status)
    return _json(_build_category_tree(cats))


async def get_review(request: web.Request) -> web.Response:
    svc = TaxonomyService(request["session"])
    cats = await svc.review_queue(request["viewer"])
    return _json([_category_node_dto(c, []) for c in cats])


async def post_resolve(request: web.Request) -> web.Response:
    svc = TaxonomyService(request["session"])
    cat_id = _path_int(request, "category_id")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    action = (body or {}).get("action")
    if action == "accept":
        cat = await svc.accept(request["viewer"], cat_id)
    elif action == "alias":
        target_id = (body or {}).get("target_id")
        if target_id is None:
            raise InvalidInput("Для action=alias нужен target_id")
        cat = await svc.alias(request["viewer"], cat_id, int(target_id))
    else:
        raise InvalidInput("action должен быть «accept» или «alias»")
    return _json(_category_node_dto(cat, []))


async def get_analytics(request: web.Request) -> web.Response:
    svc = AnalyticsService(request["session"])
    period = _period_from_query(request)
    category_id = _parse_int(request.query.get("category_id"), "category_id")
    result = await svc.analytics(request["viewer"], period, category_id)
    return _json(_analytics_dto(result))


async def get_income(request: web.Request) -> web.Response:
    svc = AnalyticsService(request["session"])
    period = _period_from_query(request)
    report = await svc.income_report(request["viewer"], period)
    return _json(_income_dto(report))


async def get_export(request: web.Request) -> web.Response:
    svc = AnalyticsService(request["session"])
    period = _period_from_query(request)
    data = await svc.export_xlsx(request["viewer"], period)
    filename = f"finance_{period.date_from.isoformat()}_{period.date_to.isoformat()}.xlsx"
    return web.Response(
        body=data,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Монтаж ──────────────────────────────────────────────────────────────────────

def register_routes(app: web.Application) -> None:
    """Регистрирует маршруты Finance. Статические подпути — раньше параметрических."""
    app.router.add_post("/api/v1/finance/import", post_import)
    app.router.add_get("/api/v1/finance/months", get_months)
    app.router.add_get("/api/v1/finance/categories/review", get_review)
    app.router.add_get("/api/v1/finance/categories", get_categories)
    app.router.add_post(
        "/api/v1/finance/categories/{category_id}/resolve", post_resolve
    )
    app.router.add_get("/api/v1/finance/analytics", get_analytics)
    app.router.add_get("/api/v1/finance/income", get_income)
    app.router.add_get("/api/v1/finance/export", get_export)
