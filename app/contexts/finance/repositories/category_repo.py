"""Репозиторий категорий + алиасов (FAMILY-скоупинг по family_id, аудит F1.2).

Видимость Finance — один `WHERE family_id=…` (весь домен FAMILY; visible_to не
прикладывается, scope/hidden_from не используются — ТЗ §11).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finance.models import (
    Category,
    CategoryAlias,
    CategoryStatus,
)


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Чтения (family-скоуп) ────────────────────────────────────────────────

    async def list_all(
        self, family_id: int, status: CategoryStatus | None = None
    ) -> list[Category]:
        q = select(Category).where(Category.family_id == family_id)
        if status is not None:
            q = q.where(Category.status == status)
        q = q.order_by(Category.parent_id.is_(None).desc(), Category.id)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get(self, family_id: int, category_id: int) -> Category | None:
        q = select(Category).where(
            Category.family_id == family_id, Category.id == category_id
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def find_root(self, family_id: int, name: str) -> Category | None:
        q = select(Category).where(
            Category.family_id == family_id,
            Category.parent_id.is_(None),
            Category.name == name,
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def find_child(
        self, family_id: int, parent_id: int, name: str
    ) -> Category | None:
        q = select(Category).where(
            Category.family_id == family_id,
            Category.parent_id == parent_id,
            Category.name == name,
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def children_of(self, parent_id: int) -> list[Category]:
        q = select(Category).where(Category.parent_id == parent_id).order_by(Category.id)
        return list((await self.session.execute(q)).scalars().all())

    async def count_children(self, parent_id: int) -> int:
        from sqlalchemy import func

        return int(
            await self.session.scalar(
                select(func.count()).select_from(Category).where(
                    Category.parent_id == parent_id
                )
            )
            or 0
        )

    async def find_alias(self, family_id: int, alias_name: str) -> CategoryAlias | None:
        q = select(CategoryAlias).where(
            CategoryAlias.family_id == family_id,
            CategoryAlias.alias_name == alias_name,
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_review(self, family_id: int) -> list[Category]:
        return await self.list_all(family_id, CategoryStatus.NEEDS_REVIEW)

    # ── Мутации ──────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        family_id: int,
        name: str,
        parent_id: int | None,
        status: CategoryStatus,
    ) -> Category:
        cat = Category(
            family_id=family_id, name=name, parent_id=parent_id, status=status
        )
        self.session.add(cat)
        await self.session.flush()
        return cat

    async def create_alias(
        self, *, family_id: int, alias_name: str, category_id: int
    ) -> CategoryAlias:
        alias = CategoryAlias(
            family_id=family_id, alias_name=alias_name, category_id=category_id
        )
        self.session.add(alias)
        await self.session.flush()
        return alias
