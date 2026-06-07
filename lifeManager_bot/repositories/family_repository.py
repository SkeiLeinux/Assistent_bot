"""
repositories/family_repository.py — CRUD для семей/групп.
"""

import random
import string
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Family


def _generate_invite_code(length: int = 6) -> str:
    """Генерирует случайный код вида 'A3XK92'."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


class FamilyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, family_id: int) -> Family | None:
        return await self.session.get(Family, family_id)

    async def get_by_invite_code(self, code: str) -> Family | None:
        """Найти семью по инвайт-коду (для вступления)."""
        result = await self.session.execute(
            select(Family).where(Family.invite_code == code.upper())
        )
        return result.scalar_one_or_none()

    async def create(self, name: str) -> Family:
        """
        Создать новую семью с уникальным инвайт-кодом.
        В теории коды могут совпасть — на практике при 6 символах
        это 36^6 = 2.1 млрд вариантов, столкновение крайне маловероятно.
        """
        family = Family(
            name=name,
            invite_code=_generate_invite_code(),
        )
        self.session.add(family)
        await self.session.flush()
        return family
