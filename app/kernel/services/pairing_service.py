"""kernel PairingService — FSM пейринга и его гварды (ТЗ §5/§6).

Инварианты: I1 (≤2 членства), I3 (инвайт одноразовый, атомарно), I4 (полная семья),
I5 (нельзя вступить, уже состоя в семье). Сервис flush'ит; commit — за вызывающим.
"""

import secrets
import string
from datetime import datetime, timezone

from app.kernel.models import Family, Invite, InviteStatus, User
from app.kernel.repositories import FamilyRepository, InviteRepository
from app.kernel.services.errors import (
    AlreadyInFamily,
    FamilyFull,
    InviteNotFound,
    InviteUnusable,
    NotInFamily,
)

MAX_FAMILY_SIZE = 2
_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_code(length: int = 8) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def _now() -> datetime:
    """Наивный UTC — согласован с server_default func.now() (SQLite CURRENT_TIMESTAMP)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PairingService:
    def __init__(self, session) -> None:
        self.session = session
        self.families = FamilyRepository(session)
        self.invites = InviteRepository(session)

    async def create_family(
        self, user: User, name: str | None = None
    ) -> tuple[Family, Invite]:
        if await self.families.get_membership(user.id):
            raise AlreadyInFamily("Вы уже состоите в семье")
        family = await self.families.create(created_by=user.id, name=name or "Наша семья")
        await self.families.add_membership(user.id, family.id)
        invite = await self.invites.create(family.id, user.id, _generate_code())
        return family, invite

    async def reissue_invite(self, user: User) -> Invite:
        membership = await self.families.get_membership(user.id)
        if membership is None:
            raise NotInFamily("Вы не состоите в семье")
        if await self.families.member_count(membership.family_id) >= MAX_FAMILY_SIZE:
            raise FamilyFull("Семья уже укомплектована")
        await self.invites.revoke_active_for_family(membership.family_id)
        return await self.invites.create(
            membership.family_id, user.id, _generate_code()
        )

    async def join(self, user: User, code: str) -> Family:
        # I5 — нельзя вступить, уже состоя в семье (в т.ч. в свою же).
        if await self.families.get_membership(user.id):
            raise AlreadyInFamily("Вы уже состоите в семье")
        invite = await self.invites.get_by_code(code)
        if invite is None:
            raise InviteNotFound("Инвайт не найден")
        if invite.status is not InviteStatus.ACTIVE:
            raise InviteUnusable("Инвайт уже использован или отозван")
        if invite.expires_at is not None and invite.expires_at < _now():
            raise InviteUnusable("Срок инвайта истёк")
        # I4 — нельзя вступить в полную семью.
        if await self.families.member_count(invite.family_id) >= MAX_FAMILY_SIZE:
            raise FamilyFull("Семья уже укомплектована")
        # I3 — одноразовость: атомарный захват active→used (выигрывает один при гонке).
        if not await self.invites.try_consume(invite.id, user.id, _now()):
            raise InviteUnusable("Инвайт уже использован")
        await self.families.add_membership(user.id, invite.family_id)
        return await self.families.get(invite.family_id)
