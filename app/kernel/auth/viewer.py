"""kernel.auth.viewer — резолв текущего зрителя из initData (ТЗ §8).

Шов standalone (ADR-0004): домены получают уже резолвнутого `viewer` и не знают
о Telegram. Вся завязка на initData/whitelist изолирована здесь.
"""

from app.config import settings
from app.kernel.auth.initdata import parse_user, validate_init_data
from app.kernel.models import User
from app.kernel.services.errors import NotWhitelisted
from app.kernel.services.identity_service import IdentityService
from app.kernel.sharing import Viewer


async def resolve_viewer(session, init_data: str) -> tuple[User, Viewer]:
    """initData → (User, Viewer). Роль «current_viewer» из ТЗ.

    InitDataError (битый/просрочен) → пробрасывается (REST: 401);
    NotWhitelisted (не в whitelist) → 403; иначе auto-provision и резолв семьи.
    """
    # .get_secret_value() — bot_token хранится SecretStr, чтобы не утекать в
    # трейсбеки и repr(settings) (см. app/config.py). Здесь он нужен в открытом
    # виде: это ключ HMAC подписи initData.
    data = validate_init_data(init_data, settings.bot_token.get_secret_value())
    tg = parse_user(data)
    tg_id = tg.get("id")

    if settings.allowed_user_ids and tg_id not in settings.allowed_user_ids:
        raise NotWhitelisted("Нет доступа к боту")

    ids = IdentityService(session)
    user = await ids.get_or_create_user(
        telegram_user_id=tg_id,
        first_name=tg.get("first_name") or tg.get("username") or f"id{tg_id}",
        username=tg.get("username"),
    )
    family_id = await ids.family_id_of(user.id)
    return user, Viewer(id=user.id, family_id=family_id)
