"""kernel.sharing.repository — базовый репозиторий с фильтром видимости.

Контракты доменов читают разделяемые сущности ТОЛЬКО через него (I10) — чтения
в обход visible_to не существует, что снимает риск утечки приватности.
"""

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing.visibility import Viewer, visible_to

T = TypeVar("T")


class ShareableRepository(Generic[T]):
    """База для репозиториев shareable-моделей. Подкласс задаёт `model`."""

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def visible_query(self, viewer: Viewer):
        """SELECT модели, отфильтрованный видимостью зрителя."""
        return select(self.model).where(visible_to(self.model, viewer))

    async def list_visible(self, viewer: Viewer) -> list[T]:
        result = await self.session.execute(self.visible_query(viewer))
        return list(result.scalars().all())

    async def get_visible(self, viewer: Viewer, entity_id: int) -> T | None:
        q = self.visible_query(viewer).where(self.model.id == entity_id)
        result = await self.session.execute(q)
        return result.scalar_one_or_none()
