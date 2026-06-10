"""S3 Reminders — критерии приёмки A1–A7 (ТЗ spec-reminders §13) + обязательный
набор тестов ADR-0003 (рестарт без потерь/дублей, misfire-грейс, recurrence-advance,
ack/snooze, PERSONAL→владельцу/FAMILY→обоим, двойной тик → один push).

Тесты на сервисном/job-слое: домен Telegram-агностичен (резолвнутый kernel-Viewer,
инъектируемый Notifier-мок). Видимость композируется через kernel-клаузы — A6
проверяется и здесь (получатели), и в test_reminders_isolation.py (импорты, A7).

Время — инжектируемые часы (FakeClock), без sleep. Notifications — мок-счётчик.
Сессии для рестарта/двойного тика берём из локального engine-фикстура (несколько
сессий на одну in-memory БД).
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.kernel.models  # noqa: F401 — регистрирует kernel-таблицы
import app.contexts.reminders.models  # noqa: F401 — регистрирует таблицу reminders
from app.db.base import Base
from app.kernel.models import Family, Membership, Scope, User
from app.kernel.sharing import Viewer

from app.contexts.reminders.models import Recurrence, Reminder, ReminderStatus
from app.contexts.reminders.services import (
    DEFAULT_GRACE_SECONDS,
    InvalidInput,
    ReminderNotFound,
    ReminderService,
    make_reminder_poll,
)
from app.contexts.reminders.services.delivery_port import Button
from app.contexts.reminders.services.reminder_poll import run_poll


# ── Инфраструктура тестов ─────────────────────────────────────────────────────

UTC0 = datetime(2026, 6, 10, 9, 0, 0)  # фикс. naive-UTC «сейчас» для детерминизма


class FakeClock:
    """Инжектируемые часы: now_fn() → текущее значение; advance двигает время."""

    def __init__(self, now: datetime = UTC0) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, **kwargs) -> None:
        self._now = self._now + timedelta(**kwargs)


class MockNotifier:
    """Мок Notifications: считает вызовы deliver, пишет получателей/кнопки/текст."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def deliver(self, recipients, text, *, buttons=None, kind: str) -> None:
        self.calls.append(
            {
                "recipient_ids": [u.id for u in recipients],
                "text": text,
                "buttons": buttons or [],
                "kind": kind,
            }
        )

    @property
    def push_count(self) -> int:
        return len(self.calls)


@pytest_asyncio.fixture
async def maker():
    """Engine + sessionmaker на одну in-memory БД (для рестарта/двойного тика)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield sessionmaker
    await engine.dispose()


@pytest_asyncio.fixture
async def session(maker):
    async with maker() as s:
        yield s


async def _family(session):
    """Двое в одной семье. Возвращает (u1, u2, fam, v1, v2)."""
    u1 = User(telegram_user_id=1, first_name="A", timezone="UTC")
    u2 = User(telegram_user_id=2, first_name="B", timezone="UTC")
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


def _poll(maker, notifier, clock, grace_seconds=DEFAULT_GRACE_SECONDS):
    """job-callable как в проде: своя сессия на тик, commit внутри."""
    return make_reminder_poll(
        maker, notifier, now_fn=clock, grace_seconds=grace_seconds
    )


async def _get(session, reminder_id) -> Reminder:
    # populate_existing → перечитать строку в await (объект мог быть продвинут другой
    # сессией; иначе доступ к expired-атрибуту лениво грузится вне greenlet).
    return await session.get(Reminder, reminder_id, populate_existing=True)


# ════════════════════════════════════════════════════════════════════════════
# A1 — CRUD через сервис + recurrence none/daily/weekly/interval
# ════════════════════════════════════════════════════════════════════════════

async def test_a1_crud_and_recurrence_kinds(session):
    _, _, _, v1, _ = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())

    # create — все виды recurrence
    daily = await svc.create_reminder(
        v1, text="Витамин D", fire_at=UTC0 + timedelta(hours=1),
        recurrence=Recurrence.DAILY, scope=Scope.PERSONAL,
    )
    once = await svc.create_reminder(
        v1, text="Разовое", fire_at=UTC0 + timedelta(hours=2),
        recurrence=Recurrence.NONE,
    )
    weekly = await svc.create_reminder(
        v1, text="Еженедельно", fire_at=UTC0 + timedelta(hours=3),
        recurrence=Recurrence.WEEKLY,
    )
    interval = await svc.create_reminder(
        v1, text="Каждые 2ч", fire_at=UTC0 + timedelta(hours=4),
        recurrence=Recurrence.INTERVAL, interval_seconds=7200,
    )
    assert {r.recurrence for r in (daily, once, weekly, interval)} == set(Recurrence)
    assert interval.interval_seconds == 7200

    # read
    listed = await svc.list_reminders(v1)
    assert len(listed) == 4
    got = await svc.get_reminder(v1, daily.id)
    assert got.text == "Витамин D"

    # update (текст + время + recurrence)
    await svc.update_reminder(v1, once.id, text="Изменено", recurrence=Recurrence.DAILY)
    upd = await svc.get_reminder(v1, once.id)
    assert upd.text == "Изменено" and upd.recurrence == Recurrence.DAILY

    # delete → CANCELLED
    await svc.delete_reminder(v1, daily.id)
    assert (await svc.get_reminder(v1, daily.id)).status == ReminderStatus.CANCELLED


async def test_a1_interval_requires_seconds(session):
    _, _, _, v1, _ = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    with pytest.raises(InvalidInput):
        await svc.create_reminder(
            v1, text="bad", fire_at=UTC0, recurrence=Recurrence.INTERVAL
        )


async def test_a1_local_time_converted_to_utc(session):
    """§12: fire_at в локальной зоне owner.timezone → next_fire_at в UTC."""
    u1, _, fam, v1, _ = await _family(session)
    u1.timezone = "Europe/Moscow"  # UTC+3
    await session.flush()
    svc = ReminderService(session, now_fn=FakeClock())
    naive_local = datetime(2026, 6, 10, 12, 0, 0)  # 12:00 МСК
    rem = await svc.create_reminder(v1, text="t", fire_at=naive_local)
    assert rem.next_fire_at == datetime(2026, 6, 10, 9, 0, 0)  # 09:00 UTC


# ════════════════════════════════════════════════════════════════════════════
# A2 — срабатывание: один раз; recurring продвигает next_fire_at; одноразовое → DONE
# ════════════════════════════════════════════════════════════════════════════

async def test_a2_oneshot_fires_once_then_done(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="разовое", fire_at=UTC0 - timedelta(minutes=1), recurrence=Recurrence.NONE,
    )
    await session.commit()
    rid = rem.id

    notifier = MockNotifier()
    poll = _poll(maker, notifier, clock)
    await poll()
    assert notifier.push_count == 1
    after = await _get(session, rid)
    assert after.status == ReminderStatus.DONE and after.next_fire_at is None
    assert after.last_fired_at == UTC0

    # повторный тик — больше не срабатывает (уже DONE)
    await poll()
    assert notifier.push_count == 1


async def test_a2_recurring_advances_next_fire_at(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="daily", fire_at=UTC0, recurrence=Recurrence.DAILY,
    )
    await session.commit()
    rid = rem.id

    notifier = MockNotifier()
    poll = _poll(maker, notifier, clock)
    await poll()
    assert notifier.push_count == 1
    after = await _get(session, rid)
    assert after.status == ReminderStatus.ACTIVE
    assert after.next_fire_at == UTC0 + timedelta(days=1)


async def test_a2_interval_and_weekly_advance(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    svc = ReminderService(session, now_fn=clock)
    weekly = await svc.create_reminder(v1, text="w", fire_at=UTC0, recurrence=Recurrence.WEEKLY)
    interval = await svc.create_reminder(
        v1, text="i", fire_at=UTC0, recurrence=Recurrence.INTERVAL, interval_seconds=3600
    )
    await session.commit()

    await _poll(maker, MockNotifier(), clock)()
    assert (await _get(session, weekly.id)).next_fire_at == UTC0 + timedelta(weeks=1)
    assert (await _get(session, interval.id)).next_fire_at == UTC0 + timedelta(seconds=3600)


# ════════════════════════════════════════════════════════════════════════════
# A3 — рестарт между созданием и сроком не теряет и не дублирует (состояние в БД)
# ════════════════════════════════════════════════════════════════════════════

async def test_a3_restart_does_not_lose_or_duplicate(maker):
    clock = FakeClock(UTC0)
    # «процесс 1»: создание напоминания на будущее, затем закрытие сессии (рестарт)
    async with maker() as s1:
        _, _, _, v1, _ = await _family(s1)
        rem = await ReminderService(s1, now_fn=clock).create_reminder(
            v1, text="переживи рестарт", fire_at=UTC0 + timedelta(minutes=10),
            recurrence=Recurrence.NONE,
        )
        await s1.commit()
        rid = rem.id

    # «процесс 2» после рестарта: время дошло до срока — состояние в БД сохранилось
    clock.set(UTC0 + timedelta(minutes=10))
    notifier = MockNotifier()
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 1  # не потеряно

    async with maker() as s2:
        assert (await s2.get(Reminder, rid)).status == ReminderStatus.DONE

    # ещё один тик (имитация второго рестарта) — без дубля
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 1  # не дублировано


# ════════════════════════════════════════════════════════════════════════════
# A4 — misfire: догон в грейсе один раз / MISSED сверх грейса / recurring к будущему
# ════════════════════════════════════════════════════════════════════════════

async def test_a4_catchup_within_grace_fires_once(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="догон", fire_at=UTC0 - timedelta(minutes=20), recurrence=Recurrence.NONE,
    )
    await session.commit()
    rid = rem.id

    notifier = MockNotifier()
    # просрочка 20 мин при грейсе 3 ч → догон один раз
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 1
    assert (await _get(session, rid)).status == ReminderStatus.DONE


async def test_a4_over_grace_oneshot_missed_no_delivery(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="проспали", fire_at=UTC0 - timedelta(hours=5), recurrence=Recurrence.NONE,
    )
    await session.commit()
    rid = rem.id

    notifier = MockNotifier()
    await _poll(maker, notifier, clock, grace_seconds=DEFAULT_GRACE_SECONDS)()
    assert notifier.push_count == 0  # сверх грейса — без доставки
    after = await _get(session, rid)
    assert after.status == ReminderStatus.MISSED and after.next_fire_at is None


async def test_a4_over_grace_recurring_advances_to_future(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    # daily, просрочено на 2 дня и 5 ч (сверх грейса) — должно встать на будущий слот
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="витамины-проспали",
        fire_at=UTC0 - timedelta(days=2, hours=5), recurrence=Recurrence.DAILY,
    )
    await session.commit()
    rid = rem.id

    notifier = MockNotifier()
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 0  # без «шторма» пропущенных доставок
    after = await _get(session, rid)
    assert after.status == ReminderStatus.ACTIVE
    assert after.next_fire_at > UTC0  # строго в будущем
    # ближайший будущий daily-слот: исходный (Jun8 04:00) + кратное суткам,
    # минимально > now (Jun10 09:00) → Jun11 04:00.
    assert after.next_fire_at == datetime(2026, 6, 11, 4, 0, 0)


async def test_a4_over_grace_recurring_then_fires_at_future_slot(maker, session):
    """После продвижения к будущему слоту recurring срабатывает штатно, один раз."""
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="d", fire_at=UTC0 - timedelta(days=2, hours=5), recurrence=Recurrence.DAILY,
    )
    await session.commit()
    rid = rem.id
    notifier = MockNotifier()
    poll = _poll(maker, notifier, clock)
    await poll()
    future_slot = (await _get(session, rid)).next_fire_at
    # доводим время до будущего слота — теперь срабатывает один раз
    clock.set(future_slot)
    await poll()
    assert notifier.push_count == 1


# ════════════════════════════════════════════════════════════════════════════
# A5 — ack/snooze: «Выполнено» закрывает одноразовое; «Отложить N» сдвигает; из сервиса
# ════════════════════════════════════════════════════════════════════════════

async def test_a5_ack_closes_oneshot(session):
    _, _, _, v1, _ = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    rem = await svc.create_reminder(v1, text="once", fire_at=UTC0, recurrence=Recurrence.NONE)
    acked = await svc.ack(v1, rem.id)
    assert acked.status == ReminderStatus.DONE and acked.next_fire_at is None


async def test_a5_ack_recurring_keeps_schedule(session):
    _, _, _, v1, _ = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    rem = await svc.create_reminder(
        v1, text="daily", fire_at=UTC0, recurrence=Recurrence.DAILY
    )
    before = rem.next_fire_at
    acked = await svc.ack(v1, rem.id)
    assert acked.status == ReminderStatus.ACTIVE and acked.next_fire_at == before


async def test_a5_snooze_shifts_next_fire_at(session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    svc = ReminderService(session, now_fn=clock)
    rem = await svc.create_reminder(v1, text="t", fire_at=UTC0, recurrence=Recurrence.NONE)
    snoozed = await svc.snooze(v1, rem.id, minutes=60)
    assert snoozed.status == ReminderStatus.ACTIVE
    assert snoozed.next_fire_at == UTC0 + timedelta(minutes=60)


async def test_a5_snooze_caps_at_next_recurring_slot(session):
    """§11: снуз за пределы штатного слота (recurring) → ближайшее из (снуз, слот)."""
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    svc = ReminderService(session, now_fn=clock)
    # daily-слот через 30 мин; снуз на 2 ч был бы дальше → берётся слот
    rem = await svc.create_reminder(
        v1, text="d", fire_at=UTC0 + timedelta(minutes=30), recurrence=Recurrence.DAILY
    )
    snoozed = await svc.snooze(v1, rem.id, minutes=120)
    assert snoozed.next_fire_at == UTC0 + timedelta(minutes=30)


async def test_a5_snooze_rejects_nonpositive(session):
    _, _, _, v1, _ = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    rem = await svc.create_reminder(v1, text="t", fire_at=UTC0, recurrence=Recurrence.NONE)
    with pytest.raises(InvalidInput):
        await svc.snooze(v1, rem.id, minutes=0)


async def test_a5_ack_snooze_404_for_invisible(session):
    """ack/snooze чужого невидимого PERSONAL → ReminderNotFound (как из REST 404)."""
    _, _, _, v1, v2 = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    rem = await svc.create_reminder(
        v1, text="личное", fire_at=UTC0, recurrence=Recurrence.NONE, scope=Scope.PERSONAL
    )
    with pytest.raises(ReminderNotFound):
        await svc.ack(v2, rem.id)


# ════════════════════════════════════════════════════════════════════════════
# A6 — видимость/доставка: PERSONAL → владельцу; FAMILY → обоим; через Notifications
# ════════════════════════════════════════════════════════════════════════════

async def test_a6_personal_delivers_to_owner_only(maker, session):
    u1, u2, _, v1, _ = await _family(session)
    clock = FakeClock()
    await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="личное", fire_at=UTC0, recurrence=Recurrence.NONE, scope=Scope.PERSONAL
    )
    await session.commit()

    notifier = MockNotifier()
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 1
    assert notifier.calls[0]["recipient_ids"] == [u1.id]
    assert u2.id not in notifier.calls[0]["recipient_ids"]


async def test_a6_family_delivers_to_both(maker, session):
    u1, u2, _, v1, _ = await _family(session)
    clock = FakeClock()
    await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="общее", fire_at=UTC0, recurrence=Recurrence.NONE, scope=Scope.FAMILY
    )
    await session.commit()

    notifier = MockNotifier()
    await _poll(maker, notifier, clock)()
    assert notifier.push_count == 1
    assert set(notifier.calls[0]["recipient_ids"]) == {u1.id, u2.id}


async def test_a6_visibility_personal_hidden_from_partner(session):
    """Видимость в Mini App: PERSONAL чужого не виден партнёру (kernel scope_visible)."""
    _, _, _, v1, v2 = await _family(session)
    svc = ReminderService(session, now_fn=FakeClock())
    await svc.create_reminder(
        v1, text="секрет", fire_at=UTC0, recurrence=Recurrence.NONE, scope=Scope.PERSONAL
    )
    assert await svc.list_reminders(v2) == []  # партнёр не видит чужое личное
    assert len(await svc.list_reminders(v1)) == 1


async def test_a6_delivery_carries_ack_snooze_buttons(maker, session):
    """Доставка идёт с обеими кнопками ack/snooze и payload rem:ack/snooze:{id} (R1)."""
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    rem = await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="t", fire_at=UTC0, recurrence=Recurrence.NONE
    )
    await session.commit()
    notifier = MockNotifier()
    await _poll(maker, notifier, clock)()
    buttons = notifier.calls[0]["buttons"]
    assert all(isinstance(b, Button) for b in buttons)
    payloads = {b.callback_data for b in buttons}
    assert payloads == {f"rem:ack:{rem.id}", f"rem:snooze:{rem.id}"}
    assert notifier.calls[0]["kind"] == "reminder"


# ════════════════════════════════════════════════════════════════════════════
# Двойной тик / гонка → один push (I4)
# ════════════════════════════════════════════════════════════════════════════

async def test_double_tick_single_push(maker, session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="t", fire_at=UTC0, recurrence=Recurrence.DAILY
    )
    await session.commit()

    notifier = MockNotifier()
    poll = _poll(maker, notifier, clock)
    # два тика на одном и том же «сейчас» — слот продвинут после первого
    await poll()
    await poll()
    assert notifier.push_count == 1


# Примечание: generic-scheduler выполняет тики СТРОГО последовательно (одна asyncio-
# задача: `await tick()` затем sleep — app/scheduler/__init__.py). Перекрывающихся
# тиков в одном event-loop не существует по построению, поэтому гарантия «один push»
# обеспечивается последовательным claim+advance в одной транзакции (I4), а не
# распределённым локом (ADR-0003 драйвер 4 — лок не нужен). Сценарий рестарта между
# тиком и доставкой покрыт test_a3_restart_does_not_lose_or_duplicate.


# ════════════════════════════════════════════════════════════════════════════
# run_poll напрямую (управление сессией снаружи) — для интеграции/документации
# ════════════════════════════════════════════════════════════════════════════

async def test_run_poll_with_external_session(session):
    _, _, _, v1, _ = await _family(session)
    clock = FakeClock()
    await ReminderService(session, now_fn=clock).create_reminder(
        v1, text="t", fire_at=UTC0, recurrence=Recurrence.NONE
    )
    await session.commit()
    notifier = MockNotifier()
    delivered = await run_poll(session, notifier, now_fn=clock)
    assert delivered == 1 and notifier.push_count == 1
