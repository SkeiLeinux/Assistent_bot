"""kernel REST (/api/v1) — Identity & Sharing (ТЗ §10).

Транспортный слой: валидирует initData (current_viewer), зовёт kernel-сервисы,
сериализует DTO. Бизнес-логика и видимость — в kernel; здесь только проводка.
Срез 1: эндпоинты еды (api.py) сняты с проводки — мини-апа отдаёт только kernel.
"""

import json
import logging

from aiohttp import web

from app.config import settings
from app.db.engine import async_session_maker
from app.kernel.auth import InitDataError, resolve_viewer
from app.kernel.services import IdentityService, PairingService
from app.kernel.services.errors import KernelError, NotInFamily, NotWhitelisted

logger = logging.getLogger(__name__)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_response(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def error_response(message: str, code: str, status: int) -> web.Response:
    return json_response({"code": code, "message": message}, status=status)


# ── DTO ──────────────────────────────────────────────────────────────────────

def _user_dto(u) -> dict:
    return {
        "id": u.id,
        "telegram_user_id": u.telegram_user_id,
        "first_name": u.first_name,
        "username": u.username,
        "is_admin": u.is_admin,
    }


def _member_dto(membership, user, viewer_id: int) -> dict:
    return {
        "user_id": user.id,
        "first_name": user.first_name,
        "username": user.username,
        "joined_at": membership.joined_at.isoformat(),
        "is_self": user.id == viewer_id,
    }


def _family_dto(family, members, viewer_id: int) -> dict:
    return {
        "id": family.id,
        "name": family.name,
        "created_at": family.created_at.isoformat(),
        "members": [_member_dto(m, u, viewer_id) for m, u in members],
    }


def _invite_dto(invite) -> dict:
    return {
        "code": invite.code,
        "status": invite.status.value,
        "created_at": invite.created_at.isoformat(),
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
    }


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


# ── Хендлеры ─────────────────────────────────────────────────────────────────

async def get_me(request: web.Request) -> web.Response:
    user = request["user"]
    ids = IdentityService(request["session"])
    fam = await ids.family_with_members(user.id)
    family_dto = _family_dto(fam[0], fam[1], user.id) if fam else None
    return json_response({"user": _user_dto(user), "family": family_dto})


async def post_family(request: web.Request) -> web.Response:
    user = request["user"]
    body = await _json_body(request)
    ps = PairingService(request["session"])
    family, invite = await ps.create_family(user, name=body.get("name"))
    members = await ps.families.members(family.id)
    return json_response(
        {"family": _family_dto(family, members, user.id), "invite": _invite_dto(invite)},
        status=201,
    )


async def get_family(request: web.Request) -> web.Response:
    user = request["user"]
    ids = IdentityService(request["session"])
    fam = await ids.family_with_members(user.id)
    if fam is None:
        raise NotInFamily("Вы не состоите в семье")
    return json_response(_family_dto(fam[0], fam[1], user.id))


async def post_invites(request: web.Request) -> web.Response:
    user = request["user"]
    invite = await PairingService(request["session"]).reissue_invite(user)
    return json_response(_invite_dto(invite), status=201)


async def post_join(request: web.Request) -> web.Response:
    user = request["user"]
    body = await _json_body(request)
    code = (body.get("code") or "").strip()
    if not code:
        return error_response("Не указан код", "bad_request", 400)
    ps = PairingService(request["session"])
    family = await ps.join(user, code)
    members = await ps.families.members(family.id)
    return json_response(_family_dto(family, members, user.id))


# ── Мидлвари ─────────────────────────────────────────────────────────────────

@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Резолв зрителя из initData; сессия живёт на время запроса, commit при успехе."""
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    async with async_session_maker() as session:
        try:
            user, viewer = await resolve_viewer(session, init_data)
        except InitDataError as exc:
            return error_response(f"Авторизация не прошла: {exc}", "auth", 401)
        except NotWhitelisted as exc:
            return error_response(exc.message, exc.code, exc.status)

        request["session"] = session
        request["user"] = user
        request["viewer"] = viewer
        response = await handler(request)
        await session.commit()
        return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Доменные ошибки kernel → аккуратный JSON с кодом/статусом."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KernelError as exc:
        return error_response(exc.message, exc.code, exc.status)
    except Exception as exc:  # noqa: BLE001 — последний рубеж
        logger.exception("Необработанная ошибка в kernel API: %s", exc)
        return error_response("Внутренняя ошибка сервера", "server", 500)


# ── Сборка ───────────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    app.router.add_get("/api/v1/me", get_me)
    app.router.add_post("/api/v1/family", post_family)
    app.router.add_get("/api/v1/family", get_family)
    app.router.add_post("/api/v1/family/invites", post_invites)
    app.router.add_post("/api/v1/family/join", post_join)
    return app


async def start_web_server() -> web.AppRunner:
    """Поднять aiohttp с kernel REST в текущем event-loop. Возвращает runner."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    logger.info(
        "Веб-сервер kernel REST слушает http://%s:%s",
        settings.webapp_host,
        settings.webapp_port,
    )
    return runner
