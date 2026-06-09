"""kernel InviteRepository — инвайты пейринга (CRUD + атомарное гашение)."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.models import Invite, InviteStatus


class InviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        family_id: int,
        created_by: int,
        code: str,
        expires_at: datetime | None = None,
    ) -> Invite:
        invite = Invite(
            family_id=family_id,
            created_by=created_by,
            code=code,
            expires_at=expires_at,
            status=InviteStatus.ACTIVE,
        )
        self.session.add(invite)
        await self.session.flush()
        return invite

    async def get_by_code(self, code: str) -> Invite | None:
        result = await self.session.execute(select(Invite).where(Invite.code == code))
        return result.scalar_one_or_none()

    async def revoke_active_for_family(self, family_id: int) -> None:
        await self.session.execute(
            update(Invite)
            .where(Invite.family_id == family_id, Invite.status == InviteStatus.ACTIVE)
            .values(status=InviteStatus.REVOKED)
        )

    async def try_consume(self, invite_id: int, used_by: int, used_at: datetime) -> bool:
        """Атомарный переход active→used (I3). True — если удалось захватить инвайт.

        Условие `status==ACTIVE` в WHERE гарантирует: при гонке двух join выиграет один
        (rowcount=1), второй получит rowcount=0.
        """
        result = await self.session.execute(
            update(Invite)
            .where(Invite.id == invite_id, Invite.status == InviteStatus.ACTIVE)
            .values(status=InviteStatus.USED, used_by=used_by, used_at=used_at)
        )
        return result.rowcount == 1
