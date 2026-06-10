"""kernel REST (/api/v1) — Identity & Sharing (ТЗ §10).

Транспортный слой: зовёт kernel-сервисы, сериализует DTO. Аутентификация (резолв
зрителя), сессия и обработка ошибок — общие для миниапы (см. web.py). Здесь только
kernel-маршруты и их DTO; маршруты Lists — в contexts.lists.api.
"""

from aiohttp import web

from app.kernel.services import IdentityService, PairingService
from app.kernel.services.errors import NotInFamily

from app.delivery.miniapp_api._http import error_response, json_body, json_response


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


# ── Хендлеры ─────────────────────────────────────────────────────────────────

async def get_me(request: web.Request) -> web.Response:
    user = request["user"]
    ids = IdentityService(request["session"])
    fam = await ids.family_with_members(user.id)
    family_dto = _family_dto(fam[0], fam[1], user.id) if fam else None
    return json_response({"user": _user_dto(user), "family": family_dto})


async def post_family(request: web.Request) -> web.Response:
    user = request["user"]
    body = await json_body(request)
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
    body = await json_body(request)
    code = (body.get("code") or "").strip()
    if not code:
        return error_response("Не указан код", "bad_request", 400)
    ps = PairingService(request["session"])
    family = await ps.join(user, code)
    members = await ps.families.members(family.id)
    return json_response(_family_dto(family, members, user.id))


# ── Монтаж ───────────────────────────────────────────────────────────────────

def register_kernel_routes(app: web.Application) -> None:
    app.router.add_get("/api/v1/me", get_me)
    app.router.add_post("/api/v1/family", post_family)
    app.router.add_get("/api/v1/family", get_family)
    app.router.add_post("/api/v1/family/invites", post_invites)
    app.router.add_post("/api/v1/family/join", post_join)
