"""delivery — change-уведомления Lists (F6.2), строго через visible_to (I2/A3).

Хук эмитится из delivery (top-слой: импортирует и Lists, и Notifications) после
успешных мутаций Lists. Проверки:
  - скрытый элемент (hidden_from) не пушится исключённому партнёру (I2/A3);
  - актор не получает своё же изменение (ТЗ §7);
  - матрица «событие→шлём»: правка текста не шлётся (Q1-Б3); created/DONE шлются.

Сервис Reminders/Telegram не задействован — change-уведомления идут текстом без
кнопок; здесь перехватываем вызов deliver через фейковый Notifier.
"""

from app.contexts.lists.models import ListType
from app.kernel.models import Family, Membership, User
from app.kernel.models.shareable import Scope
from app.kernel.sharing import Viewer

from app.delivery.bot.list_changes import (
    ListChange,
    ListChangeEvent,
    notify_list_change,
    should_notify,
)


class CapturingNotifier:
    """Перехватывает deliver(): запоминает (telegram_ids, text)."""

    def __init__(self):
        self.deliveries: list[tuple[list[int], str]] = []

    async def deliver(self, recipients, text, *, buttons=None, kind="change"):
        self.deliveries.append(([u.telegram_user_id for u in recipients], text))
        from app.notifications import DeliveryReport

        return DeliveryReport(sent=len(recipients), blocked=0, failed=0)


async def _family(session):
    u1 = User(telegram_user_id=101, first_name="Actor")
    u2 = User(telegram_user_id=102, first_name="Partner")
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
    return u1, u2, fam


# ── A3 / I2: скрытый элемент не пушится исключённому ──────────────────────────


async def test_hidden_item_not_pushed_to_excluded_partner(session):
    u1, u2, fam = await _family(session)
    notifier = CapturingNotifier()

    # Подарок: family + hidden_from=[u2] (одаряемый исключён).
    change = ListChange(
        event=ListChangeEvent.ITEM_ADDED,
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        hidden_from=[u2.id],
        list_name="Подарки",
        item_text="Сюрприз",
    )

    await notify_list_change(session, notifier, change)

    # u1 — актор (исключён как актор), u2 — исключён видимостью → никому
    assert notifier.deliveries == [] or all(
        u2.telegram_user_id not in ids for ids, _ in notifier.deliveries
    )
    # явная гарантия: u2 не получил ничего
    for ids, _ in notifier.deliveries:
        assert u2.telegram_user_id not in ids


async def test_actor_does_not_receive_own_change(session):
    u1, u2, fam = await _family(session)
    notifier = CapturingNotifier()

    change = ListChange(
        event=ListChangeEvent.ITEM_ADDED,
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        hidden_from=[],
        list_name="Покупки",
        item_text="Молоко",
    )

    await notify_list_change(session, notifier, change)

    assert len(notifier.deliveries) == 1
    ids, text = notifier.deliveries[0]
    assert ids == [u2.telegram_user_id]  # только партнёр, не актор
    assert "Молоко" in text or "Покупки" in text


async def test_family_change_reaches_partner(session):
    u1, u2, fam = await _family(session)
    notifier = CapturingNotifier()
    change = ListChange(
        event=ListChangeEvent.ITEM_DONE,
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        hidden_from=[],
        list_name="Дела",
        item_text="Помыть посуду",
    )
    await notify_list_change(session, notifier, change)
    assert len(notifier.deliveries) == 1
    assert notifier.deliveries[0][0] == [u2.telegram_user_id]


async def test_personal_change_notifies_no_one_but_owner_excluded_as_actor(session):
    """PERSONAL изменение актором — получателей нет (владелец и есть актор)."""
    u1, u2, fam = await _family(session)
    notifier = CapturingNotifier()
    change = ListChange(
        event=ListChangeEvent.ITEM_ADDED,
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.PERSONAL,
        hidden_from=[],
        list_name="Личное",
        item_text="секрет",
    )
    await notify_list_change(session, notifier, change)
    assert notifier.deliveries == []


# ── Матрица событий (Q1-Б3): правка текста не шлётся, created/DONE шлются ──────


def test_matrix_text_edit_not_notified():
    assert should_notify(ListChangeEvent.ITEM_TEXT_EDITED) is False


def test_matrix_created_and_done_notified():
    assert should_notify(ListChangeEvent.ITEM_ADDED) is True
    assert should_notify(ListChangeEvent.ITEM_DONE) is True


async def test_text_edit_event_emits_no_delivery(session):
    u1, u2, fam = await _family(session)
    notifier = CapturingNotifier()
    change = ListChange(
        event=ListChangeEvent.ITEM_TEXT_EDITED,
        actor_id=u1.id,
        family_id=fam.id,
        scope=Scope.FAMILY,
        hidden_from=[],
        list_name="Дела",
        item_text="новый текст",
    )
    await notify_list_change(session, notifier, change)
    assert notifier.deliveries == []  # шум подавлен матрицей
