"""Репозиторий списков: видимые списки + агрегаты counts (visibility-фильтр).

Все выборки идут через kernel-клаузы видимости (I10): контейнер фильтруется
`scope_visible(List)`; counts учитывают поэлементный `not_hidden(ListItem)`.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer, not_hidden, scope_visible

from app.contexts.lists.models import ItemStatus, List, ListItem, ListType


class ListRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Чтения (visibility-фильтрованные) ────────────────────────────────────

    def visible_query(self, viewer: Viewer, type_filter: ListType | None = None):
        q = select(List).where(scope_visible(List, viewer))
        if type_filter is not None:
            q = q.where(List.type == type_filter)
        return q.order_by(List.created_at)

    async def list_visible(
        self, viewer: Viewer, type_filter: ListType | None = None
    ) -> list[List]:
        result = await self.session.execute(self.visible_query(viewer, type_filter))
        return list(result.scalars().all())

    async def get_visible(self, viewer: Viewer, list_id: int) -> List | None:
        q = select(List).where(scope_visible(List, viewer), List.id == list_id)
        result = await self.session.execute(q)
        return result.scalar_one_or_none()

    async def get(self, list_id: int) -> List | None:
        return await self.session.get(List, list_id)

    async def search_by_name(self, viewer: Viewer, query: str) -> list[List]:
        """Списки, видимые зрителю, чьё название совпадает с запросом (Q1-А8)."""
        q = (
            select(List)
            .where(scope_visible(List, viewer), List.name.ilike(f"%{query}%"))
            .order_by(List.created_at.desc())
        )
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def visible_counts(self, viewer: Viewer) -> dict[int, dict[str, int]]:
        """{list_id: {'open': n, 'done': m}} по видимым зрителю элементам."""
        q = (
            select(ListItem.list_id, ListItem.status, func.count())
            .join(List, ListItem.list_id == List.id)
            .where(scope_visible(List, viewer), not_hidden(ListItem, viewer))
            .group_by(ListItem.list_id, ListItem.status)
        )
        result = await self.session.execute(q)
        counts: dict[int, dict[str, int]] = {}
        for list_id, status, n in result.all():
            bucket = counts.setdefault(list_id, {"open": 0, "done": 0})
            bucket["done" if status == ItemStatus.DONE else "open"] = n
        return counts

    # ── Мутации ──────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        type: ListType,
        name: str,
        emoji: str | None,
        owner_id: int,
        family_id: int,
        scope,
    ) -> List:
        lst = List(
            type=type,
            name=name,
            emoji=emoji,
            owner_id=owner_id,
            family_id=family_id,
            scope=scope,
        )
        self.session.add(lst)
        await self.session.flush()
        return lst

    async def delete(self, lst: List) -> None:
        await self.session.delete(lst)
        await self.session.flush()
