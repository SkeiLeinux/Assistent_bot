"""kernel IdentityService — провижининг и резолв идентичности (ТЗ §8/§9)."""

from app.config import settings
from app.kernel.models import Family, Membership, User
from app.kernel.repositories import FamilyRepository, UserRepository


class IdentityService:
    def __init__(self, session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.families = FamilyRepository(session)

    async def get_or_create_user(
        self, telegram_user_id: int, first_name: str, username: str | None = None
    ) -> User:
        """Auto-provision при первом входе whitelisted-пользователя (ТЗ §8.5)."""
        user = await self.users.get_by_telegram_id(telegram_user_id)
        if user is not None:
            return user
        is_admin = (
            settings.admin_telegram_id is not None
            and telegram_user_id == settings.admin_telegram_id
        )
        user = User(
            telegram_user_id=telegram_user_id,
            first_name=first_name or f"id{telegram_user_id}",
            username=username,
            is_admin=is_admin,
        )
        return await self.users.add(user)

    async def family_id_of(self, user_id: int) -> int | None:
        membership = await self.families.get_membership(user_id)
        return membership.family_id if membership else None

    async def family_with_members(
        self, user_id: int
    ) -> tuple[Family, list[tuple[Membership, User]]] | None:
        """Семья пользователя + её члены — для /me и GET /family. None, если не в семье."""
        family = await self.families.get_for_user(user_id)
        if family is None:
            return None
        members = await self.families.members(family.id)
        return family, members
