"""kernel FamilyRepository — семьи и членства (CRUD)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.models import Family, Membership, User


class FamilyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, created_by: int, name: str | None = None) -> Family:
        family = Family(created_by=created_by, name=name)
        self.session.add(family)
        await self.session.flush()
        return family

    async def get(self, family_id: int) -> Family | None:
        return await self.session.get(Family, family_id)

    async def get_membership(self, user_id: int) -> Membership | None:
        result = await self.session.execute(
            select(Membership).where(Membership.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user(self, user_id: int) -> Family | None:
        membership = await self.get_membership(user_id)
        return await self.get(membership.family_id) if membership else None

    async def add_membership(self, user_id: int, family_id: int) -> Membership:
        membership = Membership(user_id=user_id, family_id=family_id)
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def member_count(self, family_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.family_id == family_id)
        )
        return result.scalar_one()

    async def members(self, family_id: int) -> list[tuple[Membership, User]]:
        """Пары (Membership, User) семьи, по порядку вступления."""
        result = await self.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.family_id == family_id)
            .order_by(Membership.joined_at)
        )
        return list(result.all())
