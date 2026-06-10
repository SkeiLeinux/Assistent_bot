"""S2a Lists — критерии приёмки A1–A6 (ТЗ spec-lists §15).

Тесты на сервисном слое: видимость композируется через kernel-клаузы, домен
Telegram-агностичен (принимает резолвнутого Viewer). A6 (изоляция) проверяется
import-linter (контракт «Analytics reads Lists only via read-module»).
"""

import pytest

from app.kernel import Scope, VisibilityDTO, VisibilityError
from app.kernel.models import Family, Membership, User
from app.kernel.sharing import Viewer

from app.contexts.lists.models import ItemStatus, ListType
from app.contexts.lists.repositories import HISTORY_CAP, MAX_DEPTH
from app.contexts.lists.services import (
    CrossListNesting,
    FamilyRequired,
    InvalidParent,
    InvalidSpecificAttrs,
    ItemNotFound,
    ListNotFound,
    ListService,
    MaxDepthExceeded,
)


async def _family(session):
    u1 = User(telegram_user_id=1, first_name="A")
    u2 = User(telegram_user_id=2, first_name="B")
    session.add_all([u1, u2])
    await session.flush()
    fam = Family(created_by=u1.id)
    session.add(fam)
    await session.flush()
    session.add_all(
        [
            Membership(user_id=u1.id, family_id=fam.id),
            Membership(user_id=u2.id, family_id=fam.id),
        ]
    )
    await session.flush()
    return (
        u1,
        u2,
        fam,
        Viewer(id=u1.id, family_id=fam.id),
        Viewer(id=u2.id, family_id=fam.id),
    )


async def _mk_list(svc, viewer, type_, name, scope=Scope.FAMILY):
    return await svc.create_list(
        viewer, type=type_, name=name, emoji=None, visibility=VisibilityDTO(scope=scope)
    )


# ── A1: CRUD + иерархия ───────────────────────────────────────────────────────

async def test_crud_and_hierarchy(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)

    lst = await _mk_list(svc, v1, ListType.MOVIE, "Фильмы")
    folder = await svc.add_item(v1, lst.id, text="Фантастика")
    child = await svc.add_item(v1, lst.id, text="Дюна", parent_id=folder.id)

    assert child.parent_id == folder.id
    assert child.list_id == lst.id

    detail_list, items = await svc.get_list_detail(v1, lst.id)
    assert detail_list.name == "Фильмы"
    assert {i.id for i in items} == {folder.id, child.id}

    await svc.update_list(v1, lst.id, name="Кино")
    again, _ = await svc.get_list_detail(v1, lst.id)
    assert again.name == "Кино"

    await svc.delete_list(v1, lst.id)
    assert await svc.list_lists(v1) == []


# ── A2: первый Shareable — скрытие подарка + личный список ────────────────────

async def test_gift_hidden_from_excluded(session):
    u1, u2, _, v1, v2 = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.GIFT, "Подарки")
    normal = await svc.add_item(v1, lst.id, text="общий пункт")
    gift = await svc.add_item(v1, lst.id, text="сюрприз", hidden_from=[u2.id])

    # выдача
    _, items_v1 = await svc.get_list_detail(v1, lst.id)
    _, items_v2 = await svc.get_list_detail(v2, lst.id)
    assert {i.id for i in items_v1} == {normal.id, gift.id}
    assert {i.id for i in items_v2} == {normal.id}

    # GET item (история) → 404 для исключённого, ok владельцу
    assert await svc.item_history(v1, gift.id) is not None
    with pytest.raises(ItemNotFound):
        await svc.item_history(v2, gift.id)
    with pytest.raises(ItemNotFound):
        await svc.update_item(v2, gift.id, text="взлом")

    # поиск
    v1_hits = await svc.search(v1, "сюрприз")
    assert any(h.kind == "item" and h.item.id == gift.id for h in v1_hits)
    assert await svc.search(v2, "сюрприз") == []


async def test_personal_list_invisible_to_partner(session):
    _, _, _, v1, v2 = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.IDEA, "Личное", scope=Scope.PERSONAL)

    assert [x.id for x, _ in await svc.list_lists(v1)] == [lst.id]
    assert await svc.list_lists(v2) == []
    with pytest.raises(ListNotFound):
        await svc.get_list_detail(v2, lst.id)


# ── A7 (Q1-А8): поиск возвращает и списки по совпадению названия ──────────────

async def test_search_matches_list_names_respecting_visibility(session):
    _, _, _, v1, v2 = await _family(session)
    svc = ListService(session)
    # одинаковый регистр в названии и запросе — SQLite lower() ASCII-only по кириллице
    fam = await _mk_list(svc, v1, ListType.MOVIE, "Кино семейное")
    personal = await _mk_list(
        svc, v1, ListType.IDEA, "Кино личное", scope=Scope.PERSONAL
    )

    v1_lists = {h.list.id for h in await svc.search(v1, "Кино") if h.kind == "list"}
    assert v1_lists == {fam.id, personal.id}

    # партнёр не видит personal-список даже в поиске по названию
    v2_lists = {h.list.id for h in await svc.search(v2, "Кино") if h.kind == "list"}
    assert v2_lists == {fam.id}


# ── A3: статус ⟺ completed_at, история + кап ─────────────────────────────────

async def test_status_writes_completed_at_and_history(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.TASK, "Дела")
    item = await svc.add_item(v1, lst.id, text="купить молоко")

    await svc.update_item(v1, item.id, status=ItemStatus.DONE)
    assert item.status == ItemStatus.DONE and item.completed_at is not None

    await svc.update_item(v1, item.id, status=ItemStatus.OPEN)
    assert item.status == ItemStatus.OPEN and item.completed_at is None

    actions = [h.action.value for h in await svc.item_history(v1, item.id)]
    assert "created" in actions and actions.count("status_changed") == 2


async def test_history_is_capped(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.TASK, "Дела")
    item = await svc.add_item(v1, lst.id, text="флип")
    for i in range(HISTORY_CAP + 15):
        status = ItemStatus.DONE if i % 2 == 0 else ItemStatus.OPEN
        await svc.update_item(v1, item.id, status=status)
    assert len(await svc.item_history(v1, item.id)) == HISTORY_CAP


# ── A4: дерево (cross-list, глубина) + specific_attrs ────────────────────────

async def test_cross_list_nesting_rejected(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    a = await _mk_list(svc, v1, ListType.TASK, "A")
    b = await _mk_list(svc, v1, ListType.TASK, "B")
    item_a = await svc.add_item(v1, a.id, text="из A")
    with pytest.raises(CrossListNesting):
        await svc.add_item(v1, b.id, text="чужой родитель", parent_id=item_a.id)


async def test_max_depth_enforced(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.TASK, "Глубокий")
    parent_id = None
    for depth in range(MAX_DEPTH):
        node = await svc.add_item(v1, lst.id, text=f"d{depth}", parent_id=parent_id)
        parent_id = node.id
    with pytest.raises(MaxDepthExceeded):
        await svc.add_item(v1, lst.id, text="слишком глубоко", parent_id=parent_id)


async def test_specific_attrs_validated_by_type(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.MOVIE, "Кино")
    ok = await svc.add_item(v1, lst.id, text="Дюна", specific_attrs={"year": 2021})
    assert ok.specific_attrs == {"year": 2021}
    with pytest.raises(InvalidSpecificAttrs):
        await svc.add_item(v1, lst.id, text="плохо", specific_attrs={"bogus": 1})


# ── A5: аналитика считает только видимые DONE ────────────────────────────────

async def test_analytics_counts_only_visible_done(session):
    u1, u2, _, v1, v2 = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.GIFT, "Подарки")
    shared = await svc.add_item(v1, lst.id, text="общий")
    secret = await svc.add_item(v1, lst.id, text="секрет", hidden_from=[u2.id])
    await svc.update_item(v1, shared.id, status=ItemStatus.DONE)
    await svc.update_item(v1, secret.id, status=ItemStatus.DONE)

    a1 = await svc.analytics(v1, ListType.GIFT, None, None)
    a2 = await svc.analytics(v2, ListType.GIFT, None, None)
    assert a1["done"] == 2 and a1["total"] == 2
    assert a2["done"] == 1 and a2["total"] == 1


# ── Инварианты видимости I7/I8/I9 + предусловия ──────────────────────────────

async def test_hidden_from_invariants(session):
    u1, u2, _, v1, _ = await _family(session)
    svc = ListService(session)

    personal = await _mk_list(svc, v1, ListType.IDEA, "Личное", scope=Scope.PERSONAL)
    with pytest.raises(VisibilityError):  # I7: personal не допускает hidden_from
        await svc.add_item(v1, personal.id, text="x", hidden_from=[u2.id])

    family = await _mk_list(svc, v1, ListType.GIFT, "Семейный")
    with pytest.raises(VisibilityError):  # I8: владелец не может быть в hidden_from
        await svc.add_item(v1, family.id, text="x", hidden_from=[u1.id])
    with pytest.raises(VisibilityError):  # I9: только члены семьи
        await svc.add_item(v1, family.id, text="x", hidden_from=[999999])


async def test_create_list_requires_family(session):
    u1, _, _, _, _ = await _family(session)
    svc = ListService(session)
    lonely = Viewer(id=u1.id, family_id=None)
    with pytest.raises(FamilyRequired):
        await _mk_list(svc, lonely, ListType.TASK, "Один")


async def test_clear_done_cascades_children(session):
    # clear-done = Core-DELETE; дети DONE-узла каскадятся по FK (PRAGMA foreign_keys=ON).
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.TASK, "Дела")
    parent = await svc.add_item(v1, lst.id, text="папка")
    await svc.add_item(v1, lst.id, text="внутри", parent_id=parent.id)
    keep = await svc.add_item(v1, lst.id, text="живой")
    await svc.update_item(v1, parent.id, status=ItemStatus.DONE)

    deleted = await svc.clear_done(v1, lst.id)
    assert deleted == 1  # удалён только DONE-родитель…
    _, items = await svc.get_list_detail(v1, lst.id)
    assert {i.id for i in items} == {keep.id}  # …а ребёнок ушёл каскадом


async def test_clear_done_skips_items_hidden_from_viewer(session):
    # Q1-А6: clear-done зрителя удаляет только видимые ему DONE-элементы;
    # скрытый от него DONE-элемент (подарок) не затрагивается.
    u1, u2, _, v1, v2 = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.GIFT, "Подарки")
    shared = await svc.add_item(v1, lst.id, text="общий")
    secret = await svc.add_item(v1, lst.id, text="секрет", hidden_from=[u2.id])
    await svc.update_item(v1, shared.id, status=ItemStatus.DONE)
    await svc.update_item(v1, secret.id, status=ItemStatus.DONE)

    # партнёр (v2) чистит DONE — видит и удаляет только общий
    deleted = await svc.clear_done(v2, lst.id)
    assert deleted == 1
    # секрет цел: владелец (v1) его по-прежнему видит
    _, items_v1 = await svc.get_list_detail(v1, lst.id)
    assert {i.id for i in items_v1} == {secret.id}


async def test_move_prevents_cycle(session):
    _, _, _, v1, _ = await _family(session)
    svc = ListService(session)
    lst = await _mk_list(svc, v1, ListType.TASK, "Дерево")
    a = await svc.add_item(v1, lst.id, text="A")
    b = await svc.add_item(v1, lst.id, text="B", parent_id=a.id)
    with pytest.raises(InvalidParent):  # нельзя вложить A в своего потомка B
        await svc.update_item(v1, a.id, parent_id=b.id)
