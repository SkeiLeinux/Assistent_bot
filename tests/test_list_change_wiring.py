"""S3.1 wiring (F6.2) — порт-инверсия change-уведомлений Lists.

Покрывает:
  1. Lists эмитит ListChangeData с верными event/scope/family/hidden_from/item_text
     при успешных мутациях REST-роутов (через подставной слушатель set_change_listener);
     clear_done НЕ эмитит.
  2. delivery-фабрика make_lists_change_listener мапит ListChangeData→ListChange и
     зовёт notify_list_change (скрытый подарок не пушится исключённому).

Роуты Lists — транспортный шов (читают request['viewer']/request['session']);
драйвим их напрямую FakeRequest (как test_analytics_api). Слушатель ставим/снимаем
вокруг каждого теста (try/finally → None), чтобы не течь между тестами.
"""

import json

import pytest
from aiohttp import web

from app.kernel.models import Family, Membership, User
from app.kernel.models.shareable import Scope
from app.kernel.sharing import Viewer

from app.contexts.lists import change_events
from app.contexts.lists.api import routes as list_routes
from app.contexts.lists.change_events import ListChangeData
from app.contexts.lists.models import ItemStatus, ListType
from app.contexts.lists.services import ListService

from app.delivery.bot.list_changes import (
    ListChange,
    ListChangeEvent,
    make_lists_change_listener,
)


class FakeRequest(dict):
    """Минимальный stand-in web.Request: dict-доступ + query/match_info/json()."""

    def __init__(self, session, viewer, *, query=None, match_info=None, body=None):
        super().__init__()
        self["session"] = session
        self["viewer"] = viewer
        self.query = query or {}
        self.match_info = match_info or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise json.JSONDecodeError("no body", "", 0)
        return self._body


class CapturingListener:
    """Подставной change-слушатель: запоминает все эмитированные ListChangeData."""

    def __init__(self):
        self.events: list[ListChangeData] = []

    async def __call__(self, session, data: ListChangeData) -> None:
        self.events.append(data)


@pytest.fixture
def capture():
    """Ставит подставной слушатель на время теста, снимает в финале (изоляция)."""
    listener = CapturingListener()
    change_events.set_change_listener(listener)
    try:
        yield listener
    finally:
        change_events.set_change_listener(None)


async def _family(session):
    u1 = User(telegram_user_id=901, first_name="Actor")
    u2 = User(telegram_user_id=902, first_name="Partner")
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
    return u1, u2, fam, Viewer(id=u1.id, family_id=fam.id)


def _read(resp: web.Response):
    return resp.status, json.loads(resp.text)


# ── 1. Эмиссия из роутов Lists ────────────────────────────────────────────────


async def test_post_list_emits_list_created(session, capture):
    _, _, fam, v1 = await _family(session)
    req = FakeRequest(session, v1, body={"type": "task", "name": "Дела", "scope": "family"})
    status, _ = _read(await list_routes.post_list(req))
    assert status == 201

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "list_created"
    assert ev.actor_id == v1.id
    assert ev.family_id == fam.id
    assert ev.scope == Scope.FAMILY
    assert ev.list_name == "Дела"
    assert ev.item_text is None
    assert ev.hidden_from == []


async def test_post_item_emits_item_added(session, capture):
    _, u2, fam, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.TASK, name="Покупки", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    await session.flush()
    capture.events.clear()  # create_list через сервис не эмитит — чистим на всякий

    req = FakeRequest(
        session, v1, match_info={"list_id": str(lst.id)}, body={"text": "Молоко"}
    )
    status, _ = _read(await list_routes.post_item(req))
    assert status == 201

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "item_added"
    assert ev.actor_id == v1.id
    assert ev.family_id == fam.id
    assert ev.scope == Scope.FAMILY
    assert ev.list_name == "Покупки"
    assert ev.item_text == "Молоко"
    assert ev.hidden_from == []


async def test_post_item_carries_hidden_from(session, capture):
    _, u2, fam, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.GIFT, name="Подарки", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    await session.flush()
    capture.events.clear()

    req = FakeRequest(
        session, v1, match_info={"list_id": str(lst.id)},
        body={"text": "Сюрприз", "hidden_from": [u2.id]},
    )
    await list_routes.post_item(req)

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "item_added"
    assert ev.hidden_from == [u2.id]
    assert ev.item_text == "Сюрприз"


async def test_patch_item_status_done_emits_item_done(session, capture):
    _, _, fam, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.TASK, name="Дела", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    item = await svc.add_item(v1, lst.id, text="Помыть посуду")
    await session.flush()
    capture.events.clear()

    req = FakeRequest(
        session, v1, match_info={"item_id": str(item.id)}, body={"status": "done"}
    )
    await list_routes.patch_item(req)

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "item_done"
    assert ev.item_text == "Помыть посуду"
    assert ev.list_name == "Дела"
    assert item.status == ItemStatus.DONE


async def test_patch_item_text_emits_text_edited(session, capture):
    _, _, _, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.TASK, name="Дела", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    item = await svc.add_item(v1, lst.id, text="старый")
    await session.flush()
    capture.events.clear()

    req = FakeRequest(
        session, v1, match_info={"item_id": str(item.id)}, body={"text": "новый"}
    )
    await list_routes.patch_item(req)

    # Эмитим item_text_edited (матрица его НЕ шлёт — фильтрует хук, не дубль).
    assert len(capture.events) == 1
    assert capture.events[0].event == "item_text_edited"


async def test_delete_item_emits_item_deleted_with_snapshot(session, capture):
    _, u2, fam, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.GIFT, name="Подарки", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    item = await svc.add_item(v1, lst.id, text="Сюрприз", hidden_from=[u2.id])
    await session.flush()
    capture.events.clear()

    req = FakeRequest(session, v1, match_info={"item_id": str(item.id)})
    status = (await list_routes.delete_item(req)).status
    assert status == 204

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "item_deleted"
    assert ev.item_text == "Сюрприз"
    assert ev.list_name == "Подарки"
    assert ev.scope == Scope.FAMILY
    assert ev.family_id == fam.id
    assert ev.hidden_from == [u2.id]  # снапшот hidden_from снят ДО удаления


async def test_delete_list_emits_list_deleted_with_snapshot(session, capture):
    _, _, fam, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.TASK, name="Временный", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    await session.flush()
    capture.events.clear()

    req = FakeRequest(session, v1, match_info={"list_id": str(lst.id)})
    status = (await list_routes.delete_list(req)).status
    assert status == 204

    assert len(capture.events) == 1
    ev = capture.events[0]
    assert ev.event == "list_deleted"
    assert ev.list_name == "Временный"
    assert ev.scope == Scope.FAMILY
    assert ev.family_id == fam.id


async def test_clear_done_does_not_emit(session, capture):
    _, _, _, v1 = await _family(session)
    from app.kernel import VisibilityDTO

    svc = ListService(session)
    lst = await svc.create_list(
        v1, type=ListType.TASK, name="Дела", emoji=None,
        visibility=VisibilityDTO(scope=Scope.FAMILY),
    )
    item = await svc.add_item(v1, lst.id, text="готово")
    await svc.update_item(v1, item.id, status=ItemStatus.DONE)
    await session.flush()
    capture.events.clear()

    req = FakeRequest(session, v1, match_info={"list_id": str(lst.id)})
    status, body = _read(await list_routes.post_clear_done(req))
    assert status == 200
    assert body["deleted"] == 1
    assert capture.events == []  # массовая уборка не эмитит (F6.2)


# ── 2. delivery-фабрика: ListChangeData → ListChange → notify ─────────────────


class CapturingNotifier:
    """Перехватывает deliver(): запоминает (telegram_ids, text)."""

    def __init__(self):
        self.deliveries: list[tuple[list[int], str]] = []

    async def deliver(self, recipients, text, *, buttons=None, kind="change"):
        self.deliveries.append(([u.telegram_user_id for u in recipients], text))
        from app.notifications import DeliveryReport

        return DeliveryReport(sent=len(recipients), blocked=0, failed=0)


async def test_listener_maps_data_to_change_and_notifies(session):
    u1, u2, fam, _ = await _family(session)
    notifier = CapturingNotifier()
    listener = make_lists_change_listener(notifier)

    data = ListChangeData(
        event="item_added",
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        list_name="Покупки",
        item_text="Молоко",
    )
    await listener(session, data)

    assert len(notifier.deliveries) == 1
    ids, text = notifier.deliveries[0]
    assert ids == [u2.telegram_user_id]  # партнёр, не актор
    assert "Молоко" in text or "Покупки" in text


async def test_listener_string_event_maps_to_enum(session):
    u1, u2, fam, _ = await _family(session)
    notifier = CapturingNotifier()
    listener = make_lists_change_listener(notifier)
    # Проверяем именно маппинг str→ListChangeEvent — все строки должны быть валидны.
    for ev in ListChangeEvent:
        assert ListChangeEvent(ev.value) is ev


async def test_listener_hidden_gift_not_pushed_to_excluded(session):
    u1, u2, fam, _ = await _family(session)
    notifier = CapturingNotifier()
    listener = make_lists_change_listener(notifier)

    data = ListChangeData(
        event="item_added",
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        list_name="Подарки",
        item_text="Сюрприз",
        hidden_from=[u2.id],  # одаряемый исключён видимостью (A3/I2)
    )
    await listener(session, data)

    # u1 — актор (исключён), u2 — скрыт видимостью → никому
    for ids, _ in notifier.deliveries:
        assert u2.telegram_user_id not in ids


async def test_emit_noop_without_listener(session):
    """Дефолт-поведение порта: без слушателя emit — no-op (не падает)."""
    change_events.set_change_listener(None)
    await change_events.emit(
        session,
        ListChangeData(
            event="list_created", actor_id=1, family_id=1,
            scope=Scope.FAMILY, list_name="x",
        ),
    )  # не должно ничего поднять
