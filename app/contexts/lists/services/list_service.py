"""ListService — бизнес-логика Lists (видимость, дерево, история, валидации).

Standalone-шов (ADR-0004): принимает уже резолвнутого kernel-`Viewer`, Telegram-агностичен.
Видимость композируется из kernel-клауз (I10) в репозиториях. Транзакцией владеет
вызывающий (REST-middleware делает commit) — здесь только flush.
"""

from dataclasses import dataclass
from datetime import date

from app.db.time import utcnow_naive
from app.kernel import IdentityService, Scope, Viewer, VisibilityDTO, VisibilityError

from app.contexts.lists.models import (
    HistoryAction,
    ItemStatus,
    List,
    ListItem,
    ListItemHistory,
    ListType,
)
from app.contexts.lists.read import analytics_by_type
from app.contexts.lists.repositories import MAX_DEPTH, ItemRepository, ListRepository
from app.contexts.lists.services.errors import (
    CrossListNesting,
    FamilyRequired,
    InvalidParent,
    ItemNotFound,
    ListNotFound,
    MaxDepthExceeded,
)
from app.contexts.lists.services.specific_attrs import validate_specific_attrs

_UNSET = object()  # отличает «поле не передано» от «передано null» в PATCH


@dataclass(frozen=True)
class SearchHit:
    """Результат поиска (ТЗ §10, SearchHitDTO): item-хит несёт item+list,
    list-хит (совпадение названия списка, Q1-А8) — только list."""

    kind: str  # "item" | "list"
    list: List
    item: ListItem | None = None


@dataclass(frozen=True)
class ItemChangeSnapshot:
    """Снапшот удаляемого элемента (текст + мета контейнера + hidden_from),
    снятый ДО удаления — для эмиссии change-уведомления (F6.2, delete_item)."""

    item_text: str
    scope: Scope
    family_id: int | None
    list_name: str
    hidden_from: list[int]


class ListService:
    def __init__(self, session) -> None:
        self.session = session
        self.lists = ListRepository(session)
        self.items = ItemRepository(session)
        self.identity = IdentityService(session)

    async def _member_ids(self, viewer: Viewer) -> list[int]:
        fam = await self.identity.family_with_members(viewer.id)
        return [] if fam is None else [u.id for _, u in fam[1]]

    # ── Списки ───────────────────────────────────────────────────────────────

    async def list_lists(
        self, viewer: Viewer, type_filter: ListType | None = None
    ) -> list[tuple[List, dict]]:
        lists = await self.lists.list_visible(viewer, type_filter)
        counts = await self.lists.visible_counts(viewer)
        return [(lst, counts.get(lst.id, {"open": 0, "done": 0})) for lst in lists]

    async def create_list(
        self,
        viewer: Viewer,
        *,
        type: ListType,
        name: str,
        emoji: str | None,
        visibility: VisibilityDTO,
    ) -> List:
        if viewer.family_id is None:
            raise FamilyRequired("Сначала создай семью или вступи в неё (/family).")
        if visibility.hidden_from:
            # На уровне списка hidden_from нет — скрытие поэлементное (§4.1).
            raise VisibilityError("hidden_from задаётся на элементе, не на списке")
        return await self.lists.create(
            type=type,
            name=name.strip(),
            emoji=(emoji or None),
            owner_id=viewer.id,
            family_id=viewer.family_id,
            scope=visibility.scope,
        )

    async def get_list_detail(
        self, viewer: Viewer, list_id: int
    ) -> tuple[List, list[ListItem]]:
        lst = await self.lists.get_visible(viewer, list_id)
        if lst is None:
            raise ListNotFound("Список не найден")
        items = await self.items.list_visible_of(viewer, list_id)
        return lst, items

    async def update_list(
        self,
        viewer: Viewer,
        list_id: int,
        *,
        name: str | None = None,
        emoji=_UNSET,
        scope: Scope | None = None,
    ) -> List:
        lst = await self.lists.get_visible(viewer, list_id)
        if lst is None:
            raise ListNotFound("Список не найден")
        if name is not None:
            lst.name = name.strip()
        if emoji is not _UNSET:
            lst.emoji = emoji or None
        if scope is not None:
            lst.scope = scope
        await self.session.flush()
        return lst

    async def delete_list(
        self, viewer: Viewer, list_id: int
    ) -> tuple[Scope, int | None, str]:
        """Удаляет список; возвращает снапшот (scope, family_id, name) ДО удаления
        для change-уведомления (F6.2). Снапшот берётся, пока сущность ещё жива."""
        lst = await self.lists.get_visible(viewer, list_id)
        if lst is None:
            raise ListNotFound("Список не найден")
        snapshot = (lst.scope, lst.family_id, lst.name)
        await self.lists.delete(lst)
        return snapshot

    async def clear_done(self, viewer: Viewer, list_id: int) -> int:
        lst = await self.lists.get_visible(viewer, list_id)
        if lst is None:
            raise ListNotFound("Список не найден")
        return await self.items.clear_done(viewer, list_id)

    # ── Элементы ─────────────────────────────────────────────────────────────

    async def add_item(
        self,
        viewer: Viewer,
        list_id: int,
        *,
        text: str,
        parent_id: int | None = None,
        specific_attrs: dict | None = None,
        hidden_from: list[int] | None = None,
    ) -> ListItem:
        lst = await self.lists.get_visible(viewer, list_id)
        if lst is None:
            raise ListNotFound("Список не найден")

        if parent_id is not None:
            await self._check_parent(viewer, list_id, parent_id)
            if await self.items.depth_of(parent_id) + 1 > MAX_DEPTH:
                raise MaxDepthExceeded(f"Превышена глубина вложенности ({MAX_DEPTH})")

        attrs = validate_specific_attrs(lst.type, specific_attrs)
        hidden_ids = await self._validate_item_hidden(lst, viewer.id, viewer, hidden_from)

        item = await self.items.create(
            list_id=list_id,
            added_by=viewer.id,
            text=text.strip(),
            parent_id=parent_id,
            specific_attrs=attrs,
        )
        if hidden_ids:
            await self.items.set_hidden_from(item.id, hidden_ids)
        await self.items.add_history(item.id, viewer.id, HistoryAction.CREATED)
        return item

    async def update_item(
        self,
        viewer: Viewer,
        item_id: int,
        *,
        text=_UNSET,
        status: ItemStatus | None = None,
        specific_attrs=_UNSET,
        hidden_from=_UNSET,
        parent_id=_UNSET,
    ) -> ListItem:
        item = await self.items.get_visible(viewer, item_id)
        if item is None:
            raise ItemNotFound("Элемент не найден")  # 404 скрывает существование
        lst = await self.lists.get(item.list_id)

        events: list[tuple[HistoryAction, str | None]] = []

        if text is not _UNSET and text is not None:
            new_text = text.strip()
            if new_text and new_text != item.text:
                item.text = new_text
                events.append((HistoryAction.EDITED, "текст"))

        if specific_attrs is not _UNSET:
            item.specific_attrs = validate_specific_attrs(lst.type, specific_attrs)
            events.append((HistoryAction.EDITED, "поля"))

        if status is not None and status != item.status:
            old = item.status
            item.status = status
            item.completed_at = utcnow_naive() if status == ItemStatus.DONE else None
            events.append(
                (HistoryAction.STATUS_CHANGED, f"{old.value}→{status.value}")
            )

        if hidden_from is not _UNSET:
            ids = await self._validate_item_hidden(
                lst, item.added_by, viewer, hidden_from
            )
            await self.items.set_hidden_from(item.id, ids)
            events.append((HistoryAction.EDITED, "видимость"))

        if parent_id is not _UNSET and parent_id != item.parent_id:
            await self._move_item(viewer, item, parent_id)
            events.append((HistoryAction.MOVED, None))

        await self.session.flush()
        for action, detail in events:
            await self.items.add_history(item.id, viewer.id, action, detail)
        return item

    async def delete_item(
        self, viewer: Viewer, item_id: int
    ) -> "ItemChangeSnapshot":
        """Удаляет элемент; возвращает снапшот (текст + мета контейнера +
        hidden_from) ДО удаления для change-уведомления (F6.2)."""
        item = await self.items.get_visible(viewer, item_id)
        if item is None:
            raise ItemNotFound("Элемент не найден")
        lst = await self.lists.get(item.list_id)
        snapshot = ItemChangeSnapshot(
            item_text=item.text,
            scope=lst.scope,
            family_id=lst.family_id,
            list_name=lst.name,
            hidden_from=await self.items.hidden_from_ids(item.id),
        )
        await self.items.delete(item)
        return snapshot

    async def item_history(
        self, viewer: Viewer, item_id: int
    ) -> list[ListItemHistory]:
        if await self.items.get_visible(viewer, item_id) is None:
            raise ItemNotFound("Элемент не найден")
        return await self.items.history(item_id)

    async def hidden_from_ids(self, item_id: int) -> list[int]:
        return await self.items.hidden_from_ids(item_id)

    # ── Read-хелперы для change-уведомлений (F6.2) ───────────────────────────
    # Возвращают мету контейнера-списка для сборки ListChangeData в api/routes.
    # Существующие сигнатуры не трогаются — это аддитивные чтения.

    async def list_meta(self, list_id: int) -> tuple[Scope, int | None, str] | None:
        """(scope, family_id, name) контейнера-списка для эмиссии change-события.

        Без проверки видимости — зовётся после успешной мутации, на уже
        авторизованном пути. None, если списка нет.
        """
        lst = await self.lists.get(list_id)
        if lst is None:
            return None
        return lst.scope, lst.family_id, lst.name

    # ── Поиск / аналитика ────────────────────────────────────────────────────

    async def search(self, viewer: Viewer, query: str) -> list[SearchHit]:
        """Поиск по видимым элементам (текст) и по названиям видимых списков (Q1-А8)."""
        items = await self.items.search(viewer, query)
        list_cache: dict[int, List] = {}
        for lid in {i.list_id for i in items}:
            list_cache[lid] = await self.lists.get(lid)
        hits = [SearchHit("item", list_cache[i.list_id], i) for i in items]
        for lst in await self.lists.search_by_name(viewer, query):
            hits.append(SearchHit("list", lst))
        return hits

    async def analytics(
        self,
        viewer: Viewer,
        list_type: ListType,
        date_from: date | None,
        date_to: date | None,
    ) -> dict:
        return await analytics_by_type(
            self.session, viewer, list_type, date_from, date_to
        )

    # ── Внутренние проверки ──────────────────────────────────────────────────

    async def _check_parent(
        self, viewer: Viewer, list_id: int, parent_id: int
    ) -> None:
        parent = await self.items.get(parent_id)
        if parent is None or parent.list_id != list_id:
            raise CrossListNesting("Родитель из другого списка или не существует")
        if await self.items.get_visible(viewer, parent_id) is None:
            # Родитель скрыт от зрителя — для него его не существует.
            raise ItemNotFound("Родительский элемент не найден")

    async def _move_item(
        self, viewer: Viewer, item: ListItem, new_parent_id: int | None
    ) -> None:
        if new_parent_id is not None:
            await self._check_parent(viewer, item.list_id, new_parent_id)
            if await self.items.is_ancestor_or_self(item.id, new_parent_id):
                raise InvalidParent("Нельзя вложить элемент в себя или своего потомка")
            if await self.items.depth_of(new_parent_id) + 1 > MAX_DEPTH:
                raise MaxDepthExceeded(f"Превышена глубина вложенности ({MAX_DEPTH})")
        item.parent_id = new_parent_id

    async def _validate_item_hidden(
        self,
        lst: List,
        owner_id: int,
        viewer: Viewer,
        hidden_from: list[int] | None,
    ) -> list[int]:
        """I7 (hidden_from только при FAMILY) + I8/I9 через kernel-валидатор."""
        if not hidden_from:
            return []
        if lst.scope != Scope.FAMILY:
            raise VisibilityError("scope=personal не допускает hidden_from (I7)")
        dto = VisibilityDTO(scope=Scope.FAMILY, hidden_from=hidden_from)
        dto.validate_against(owner_id, await self._member_ids(viewer))  # I8, I9
        return list(dict.fromkeys(hidden_from))  # дедуп с сохранением порядка
