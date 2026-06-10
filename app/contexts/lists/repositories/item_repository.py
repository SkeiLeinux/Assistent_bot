"""Репозиторий элементов: дерево, поиск, история, поэлементная видимость.

Видимость элемента композируется (ТЗ §6/§7): контейнер `scope_visible(List)` +
элемент `not_hidden(ListItem)` через join по list_id. JSON-доступ (`specific_attrs`)
изолирован здесь (ADR-0005). Здесь же — кап истории (L3) и инварианты дерева.
"""

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer, not_hidden, scope_visible

from app.contexts.lists.models import (
    HistoryAction,
    ItemStatus,
    List,
    ListItem,
    ListItemHistory,
)

MAX_DEPTH = 10        # предельная глубина дерева элементов (I3)
HISTORY_CAP = 50      # последних событий истории на элемент (L3, кап под диск VPS)


class ItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Базовый visibility-фильтр элементов ──────────────────────────────────

    def visible_query(self, viewer: Viewer):
        return (
            select(ListItem)
            .join(List, ListItem.list_id == List.id)
            .where(scope_visible(List, viewer), not_hidden(ListItem, viewer))
        )

    async def get_visible(self, viewer: Viewer, item_id: int) -> ListItem | None:
        q = self.visible_query(viewer).where(ListItem.id == item_id)
        result = await self.session.execute(q)
        return result.scalar_one_or_none()

    async def get(self, item_id: int) -> ListItem | None:
        return await self.session.get(ListItem, item_id)

    async def list_visible_of(self, viewer: Viewer, list_id: int) -> list[ListItem]:
        q = (
            self.visible_query(viewer)
            .where(ListItem.list_id == list_id)
            .order_by(ListItem.parent_id.nulls_first(), ListItem.position, ListItem.id)
        )
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def search(self, viewer: Viewer, query: str) -> list[ListItem]:
        q = self.visible_query(viewer).where(ListItem.text.ilike(f"%{query}%"))
        result = await self.session.execute(q.order_by(ListItem.updated_at.desc()))
        return list(result.scalars().all())

    # ── Дерево: глубина и циклы (I2/I3) ──────────────────────────────────────

    async def depth_of(self, item_id: int) -> int:
        """Число узлов от корня до item включительно (корневой = 1)."""
        depth = 0
        cur: int | None = item_id
        for _ in range(MAX_DEPTH + 2):
            if cur is None:
                break
            it = await self.session.get(ListItem, cur)
            if it is None:
                break
            depth += 1
            if it.parent_id is None:
                break
            cur = it.parent_id
        return depth

    async def is_ancestor_or_self(self, ancestor_id: int, node_id: int) -> bool:
        """True, если ancestor_id == node_id или предок node_id (для защиты от циклов)."""
        cur: int | None = node_id
        for _ in range(MAX_DEPTH + 2):
            if cur is None:
                return False
            if cur == ancestor_id:
                return True
            it = await self.session.get(ListItem, cur)
            if it is None:
                return False
            cur = it.parent_id
        return False

    async def _next_position(self, list_id: int, parent_id: int | None) -> int:
        q = select(func.max(ListItem.position)).where(
            ListItem.list_id == list_id,
            ListItem.parent_id.is_(parent_id) if parent_id is None
            else ListItem.parent_id == parent_id,
        )
        result = await self.session.execute(q)
        return (result.scalar() or 0) + 1

    # ── Мутации элементов ────────────────────────────────────────────────────

    async def create(
        self,
        *,
        list_id: int,
        added_by: int,
        text: str,
        parent_id: int | None,
        specific_attrs: dict | None,
    ) -> ListItem:
        item = ListItem(
            list_id=list_id,
            added_by=added_by,
            text=text,
            parent_id=parent_id,
            specific_attrs=specific_attrs,
            status=ItemStatus.OPEN,
            position=await self._next_position(list_id, parent_id),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def delete(self, item: ListItem) -> None:
        await self.session.delete(item)
        await self.session.flush()

    async def clear_done(self, viewer: Viewer, list_id: int) -> int:
        # Q1-А6: чистятся только DONE-элементы, видимые зрителю; скрытые от него
        # (not_hidden) не затрагиваются. Дети удалённых узлов уходят каскадом по FK.
        result = await self.session.execute(
            delete(ListItem)
            .where(
                ListItem.list_id == list_id,
                ListItem.status == ItemStatus.DONE,
                not_hidden(ListItem, viewer),
            )
            .returning(ListItem.id)
        )
        return len(result.fetchall())

    # ── hidden_from (H3-a, прямой доступ к ассоциативной таблице) ─────────────

    async def hidden_from_ids(self, item_id: int) -> list[int]:
        tbl = ListItem.hidden_from_table()
        result = await self.session.execute(
            select(tbl.c.user_id).where(tbl.c.entity_id == item_id)
        )
        return [row[0] for row in result.all()]

    async def set_hidden_from(self, item_id: int, user_ids: list[int]) -> None:
        tbl = ListItem.hidden_from_table()
        await self.session.execute(delete(tbl).where(tbl.c.entity_id == item_id))
        if user_ids:
            await self.session.execute(
                insert(tbl),
                [{"entity_id": item_id, "user_id": uid} for uid in user_ids],
            )
        await self.session.flush()

    # ── История (action-лог + кап) ───────────────────────────────────────────

    async def add_history(
        self,
        item_id: int,
        user_id: int,
        action: HistoryAction,
        detail: str | None = None,
    ) -> None:
        self.session.add(
            ListItemHistory(
                item_id=item_id, user_id=user_id, action=action, detail=detail
            )
        )
        await self.session.flush()
        keep = (
            select(ListItemHistory.id)
            .where(ListItemHistory.item_id == item_id)
            .order_by(ListItemHistory.id.desc())
            .limit(HISTORY_CAP)
        )
        await self.session.execute(
            delete(ListItemHistory).where(
                ListItemHistory.item_id == item_id,
                ListItemHistory.id.not_in(keep.scalar_subquery()),
            )
        )

    async def history(self, item_id: int) -> list[ListItemHistory]:
        result = await self.session.execute(
            select(ListItemHistory)
            .where(ListItemHistory.item_id == item_id)
            .order_by(ListItemHistory.at.desc(), ListItemHistory.id.desc())
        )
        return list(result.scalars().all())
