"""S3 Analytics — ретро-дайджест: композиция per-recipient + digest-job (A2–A4, A6).

A2: scheduler триггерит композицию; deliver вызывается для обоих; last_sent_at
    обновляется; повторный тик не дублирует.
A3: состав = Списки+Финансы+Питание, БЕЗ напоминаний; пустые секции скрыты.
A4: скрытые элементы и чужое питание не в дайджесте получателя (per-recipient).
A6: enabled=false → дайджест не шлётся.

Часы — фейковые (передаём now). Notifications — мок-deliver со счётчиком (ни одного
живого Telegram-вызова). Источник «изменений списков» — lists.read (по полям
ListItem; recent-changes-проекции нет → обход через analytics_by_type, Q-an-1).
"""

from datetime import date, datetime, time
from decimal import Decimal

from app.kernel.models import Family, Membership, User
from app.kernel.sharing import Viewer

from app.contexts.lists.models import ItemStatus, List, ListItem, ListType
from app.contexts.finance.models import ActualAmount, Category
from app.contexts.finance.models.enums import CategoryStatus
from app.contexts.nutrition.models import FoodEntry, NutritionGoals

from app.analytics.digest import compose_digest, render_text
from app.analytics.schedule import Cadence, DigestSchedule, default_schedules
from app.analytics.job import run_digest_job


# UTC, чтобы наивный-UTC `now`/`created_at` совпадал со стенными часами и днями —
# логика созревания/группировки изолирована от DST (зональный путь самого
# преобразования проверяется отдельно ниже, test_job_owner_timezone_window).
TZ = "UTC"


class DeliverSpy:
    """Мок Notifications.deliver со счётчиком (A2). Сигнатура job: (chat_ids, text)."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[int], str]] = []

    async def __call__(self, chat_ids, text) -> None:
        self.calls.append((list(chat_ids), text))

    @property
    def count(self) -> int:
        return len(self.calls)


async def _family(session):
    u1 = User(telegram_user_id=201, first_name="Катя", timezone=TZ)
    u2 = User(telegram_user_id=202, first_name="Сережа", timezone=TZ)
    session.add_all([u1, u2])
    await session.flush()
    fam = Family(created_by=u1.id)
    session.add(fam)
    await session.flush()
    session.add_all([
        Membership(user_id=u1.id, family_id=fam.id),
        Membership(user_id=u2.id, family_id=fam.id),
    ])
    await session.flush()
    return fam, u1, u2


async def _list_done(session, fam, owner, list_type, when):
    lst = List(type=list_type, name=list_type.value, owner_id=owner.id, family_id=fam.id)
    session.add(lst)
    await session.flush()
    item = ListItem(
        list_id=lst.id, added_by=owner.id, text="done", status=ItemStatus.DONE,
        created_at=when, completed_at=when,
    )
    session.add(item)
    await session.flush()
    return lst, item


async def _list_added(session, fam, owner, list_type, when):
    """OPEN-элемент, добавленный в период (created_at=when), без выполнения."""
    lst = List(type=list_type, name=list_type.value, owner_id=owner.id, family_id=fam.id)
    session.add(lst)
    await session.flush()
    item = ListItem(
        list_id=lst.id, added_by=owner.id, text="new", status=ItemStatus.OPEN,
        created_at=when,
    )
    session.add(item)
    await session.flush()
    return lst, item


async def _finance(session, fam, amount, day):
    parent = Category(name="Едовое", family_id=fam.id, status=CategoryStatus.ACTIVE)
    session.add(parent)
    await session.flush()
    child = Category(name="Продукты", parent_id=parent.id, family_id=fam.id,
                     status=CategoryStatus.ACTIVE)
    session.add(child)
    await session.flush()
    session.add(ActualAmount(category_id=child.id, date=day, amount=Decimal(amount)))
    await session.flush()
    return parent, child


async def _food(session, user, kcal, when):
    session.add(FoodEntry(
        user_id=user.id, created_at=when, description="meal",
        kcal=kcal, protein=10, fat=10, carbs=10,
    ))
    await session.flush()


# ── A3: состав = Списки+Финансы+Питание; без напоминаний; пустые скрыты ────────

async def test_digest_composition_three_spheres_no_reminders(session):
    fam, u1, u2 = await _family(session)
    await _list_done(session, fam, u1, ListType.TASK, datetime(2026, 6, 10, 9, 0))
    await _finance(session, fam, "1500.00", date(2026, 6, 10))
    await _food(session, u1, 700, datetime(2026, 6, 10, 12, 0))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    spheres = {s["sphere"] for s in dto["sections"]}
    assert spheres == {"lists", "finance", "nutrition"}
    # Никаких напоминаний (I5/A3).
    assert "reminders" not in spheres
    assert "reminder" not in render_text(dto, "Катя").lower()


async def test_digest_lists_section_carries_added_and_done(session):
    """Q-an-1: секция списков несёт added/done по типам (через recent_changes).

    Тип только с добавлением (без выполнения) тоже попадает в секцию.
    """
    fam, u1, u2 = await _family(session)
    # TASK: 1 выполнено (added=1, done=1, т.к. _list_done ставит created_at=when).
    await _list_done(session, fam, u1, ListType.TASK, datetime(2026, 6, 10, 9, 0))
    # IDEA: только добавлено в период (added=1, done=0) — тоже должно показаться.
    await _list_added(session, fam, u1, ListType.IDEA, datetime(2026, 6, 10, 10, 0))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    lists = next(s for s in dto["sections"] if s["sphere"] == "lists")
    by_type = {t["type"]: t for t in lists["by_type"]}
    assert by_type["task"]["added"] == 1 and by_type["task"]["done"] == 1
    assert by_type["idea"]["added"] == 1 and by_type["idea"]["done"] == 0

    text = render_text(dto, "Катя")
    assert "добавлено 1, выполнено 1" in text  # TASK
    assert "добавлено 1" in text               # IDEA (added-only)


async def test_digest_nutrition_carries_goal_and_renders(session):
    """Q-an-2: дайджест несёт цель КБЖУ; текст показывает «факт / цель ккал»."""
    fam, u1, u2 = await _family(session)
    await _food(session, u1, 700, datetime(2026, 6, 10, 12, 0))
    session.add(NutritionGoals(
        user_id=u1.id, target_kcal=2000, target_protein=120,
        target_fat=70, target_carbs=250,
    ))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    nut = next(s for s in dto["sections"] if s["sphere"] == "nutrition")
    assert nut["goal"] == {"kcal": 2000.0, "protein": 120.0, "fat": 70.0, "carbs": 250.0}
    assert "700 / 2000 ккал" in render_text(dto, "Катя")


async def test_digest_nutrition_goal_none_renders_fact_only(session):
    """Нет целей → goal=None; текст показывает только факт (как раньше)."""
    fam, u1, u2 = await _family(session)
    await _food(session, u1, 700, datetime(2026, 6, 10, 12, 0))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    nut = next(s for s in dto["sections"] if s["sphere"] == "nutrition")
    assert nut["goal"] is None
    text = render_text(dto, "Катя")
    assert "700 ккал" in text       # плоский факт
    assert "700 /" not in text      # без «факт / цель»


async def test_digest_hides_empty_sections(session):
    """A3: сфера без данных в периоде — скрыта; не пустой раздел."""
    fam, u1, u2 = await _family(session)
    # Только питание; списки/финансы пусты.
    await _food(session, u1, 500, datetime(2026, 6, 10, 12, 0))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    spheres = {s["sphere"] for s in dto["sections"]}
    assert spheres == {"nutrition"}


async def test_digest_fully_empty_returns_none(session):
    """ТЗ §11: полностью пустой дайджест → compose возвращает None (не шлём)."""
    fam, u1, u2 = await _family(session)
    await session.commit()
    viewer = Viewer(id=u1.id, family_id=fam.id)
    dto = await compose_digest(
        session, viewer, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ
    )
    assert dto is None


# ── A4: per-recipient — чужое питание / скрытые элементы исключены ────────────

async def test_digest_per_recipient_personal_food(session):
    fam, u1, u2 = await _family(session)
    await _food(session, u1, 600, datetime(2026, 6, 10, 12, 0))
    await _food(session, u2, 1234, datetime(2026, 6, 10, 13, 0))
    await session.commit()

    v1 = Viewer(id=u1.id, family_id=fam.id)
    v2 = Viewer(id=u2.id, family_id=fam.id)
    d1 = await compose_digest(session, v1, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ)
    d2 = await compose_digest(session, v2, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ)

    nut1 = next(s for s in d1["sections"] if s["sphere"] == "nutrition")
    nut2 = next(s for s in d2["sections"] if s["sphere"] == "nutrition")
    assert nut1["kcal"] == 600.0
    assert nut2["kcal"] == 1234.0


async def test_digest_excludes_hidden_gift(session):
    fam, u1, u2 = await _family(session)
    lst = List(type=ListType.GIFT, name="gift", owner_id=u1.id, family_id=fam.id)
    session.add(lst)
    await session.flush()
    secret = ListItem(
        list_id=lst.id, added_by=u1.id, text="секрет", status=ItemStatus.DONE,
        created_at=datetime(2026, 6, 10, 8, 0),
        completed_at=datetime(2026, 6, 10, 9, 0),
    )
    session.add(secret)
    await session.flush()
    from sqlalchemy import insert
    await session.execute(
        insert(ListItem.hidden_from_table()).values(entity_id=secret.id, user_id=u2.id)
    )
    await session.commit()

    v1 = Viewer(id=u1.id, family_id=fam.id)
    v2 = Viewer(id=u2.id, family_id=fam.id)
    d1 = await compose_digest(session, v1, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ)
    d2 = await compose_digest(session, v2, date(2026, 6, 10), date(2026, 6, 10), tz_name=TZ)

    # u1 видит подарок (есть секция списков), u2 — нет (его дайджест пуст → None).
    assert d1 is not None
    lists1 = next(s for s in d1["sections"] if s["sphere"] == "lists")
    gift = next(t for t in lists1["by_type"] if t["type"] == "gift")
    assert gift["added"] == 1
    assert gift["done"] == 1
    assert d2 is None  # для u2 нечего показать → не шлём


# ── A2: digest-job — созревание, deliver обоим, last_sent_at, без дублей ───────

async def _seed_schedules(session, fam):
    seeds = default_schedules(family_id=fam.id)
    session.add_all(seeds)
    await session.flush()
    return seeds


async def test_job_delivers_to_both_and_updates_last_sent(session):
    fam, u1, u2 = await _family(session)
    await _list_done(session, fam, u1, ListType.TASK, datetime(2026, 6, 10, 9, 0))
    await _food(session, u1, 600, datetime(2026, 6, 10, 12, 0))
    await _seed_schedules(session, fam)
    await session.commit()

    spy = DeliverSpy()

    # now = daily-время наступило 2026-06-10 23:59 (tz=UTC); weekly (вс) — НЕ сегодня (ср).
    now = datetime(2026, 6, 10, 23, 59)
    assert now.weekday() != 6
    await run_digest_job(session, deliver=spy, now=now)

    # Daily созрел → push ОБОИМ партнёрам (I4). Композиция per-recipient различается:
    # у u1 есть личное питание, у u2 — нет → тексты разные → раздельные deliver-вызовы.
    reached = {cid for chat_ids, _ in spy.calls for cid in chat_ids}
    assert reached == {u1.telegram_user_id, u2.telegram_user_id}
    # У u1 — секция «Питание» (личное); у u2 — нет (per-recipient, A4).
    u1_text = next(t for ids, t in spy.calls if u1.telegram_user_id in ids)
    u2_text = next(t for ids, t in spy.calls if u2.telegram_user_id in ids)
    assert "Питание" in u1_text
    assert "Питание" not in u2_text
    # Общие FAMILY-сферы у обоих (Списки/Финансы — TASK выполнен).
    assert "Списки" in u1_text and "Списки" in u2_text

    # last_sent_at обновлён у daily, у weekly — нет (не созрел).
    daily = next(s for s in await _all_schedules(session, fam.id)
                 if s.cadence == Cadence.DAILY)
    weekly = next(s for s in await _all_schedules(session, fam.id)
                  if s.cadence == Cadence.WEEKLY)
    assert daily.last_sent_at == now
    assert weekly.last_sent_at is None


async def test_job_second_tick_same_day_no_duplicate(session):
    fam, u1, u2 = await _family(session)
    await _food(session, u1, 600, datetime(2026, 6, 10, 12, 0))
    await _seed_schedules(session, fam)
    await session.commit()

    spy = DeliverSpy()
    now = datetime(2026, 6, 10, 23, 59)
    await run_digest_job(session, deliver=spy, now=now)
    assert spy.count == 1

    # Повторный тик в тот же день (позже) — last_sent_at сегодня → не дублируется.
    await run_digest_job(session, deliver=spy, now=datetime(2026, 6, 10, 23, 59, 40))
    assert spy.count == 1  # без дублей (edge §11)


async def test_job_disabled_schedule_not_sent(session):
    """A6: enabled=false → job не шлёт."""
    fam, u1, u2 = await _family(session)
    await _food(session, u1, 600, datetime(2026, 6, 10, 12, 0))
    seeds = await _seed_schedules(session, fam)
    for s in seeds:
        s.enabled = False
    await session.commit()

    spy = DeliverSpy()
    await run_digest_job(session, deliver=spy, now=datetime(2026, 6, 10, 23, 59))
    assert spy.count == 0


async def test_job_empty_digest_not_delivered(session):
    """ТЗ §11: созрело, но данных нет → deliver не вызывается, но last_sent_at двигается."""
    fam, u1, u2 = await _family(session)
    await _seed_schedules(session, fam)  # никаких данных
    await session.commit()

    spy = DeliverSpy()
    now = datetime(2026, 6, 10, 23, 59)
    await run_digest_job(session, deliver=spy, now=now)
    assert spy.count == 0
    # last_sent_at сдвинут, чтобы не молотить созревшее расписание каждый тик.
    daily = next(s for s in await _all_schedules(session, fam.id)
                 if s.cadence == Cadence.DAILY)
    assert daily.last_sent_at == now


async def test_job_owner_timezone_window(session):
    """F6.4: окно созревания — в таймзоне ВЛАДЕЛЬЦА семьи (Amsterdam, +2 в июне).

    daily 23:59 локального наступает при наивном-UTC 21:59 (DST), но НЕ при 12:00 UTC.
    """
    u1 = User(telegram_user_id=211, first_name="Катя", timezone="Europe/Amsterdam")
    u2 = User(telegram_user_id=212, first_name="Сережа", timezone="Europe/Amsterdam")
    session.add_all([u1, u2])
    await session.flush()
    fam = Family(created_by=u1.id)  # владелец u1 → его зона Amsterdam
    session.add(fam)
    await session.flush()
    session.add_all([
        Membership(user_id=u1.id, family_id=fam.id),
        Membership(user_id=u2.id, family_id=fam.id),
    ])
    await session.flush()
    await _food(session, u1, 600, datetime(2026, 6, 10, 12, 0))
    await _seed_schedules(session, fam)
    await session.commit()

    # 12:00 UTC = 14:00 Amsterdam < 23:59 → daily НЕ созрел.
    spy_early = DeliverSpy()
    await run_digest_job(session, deliver=spy_early, now=datetime(2026, 6, 10, 12, 0))
    assert spy_early.count == 0

    # 21:59 UTC = 23:59 Amsterdam → daily созрел.
    spy = DeliverSpy()
    await run_digest_job(session, deliver=spy, now=datetime(2026, 6, 10, 21, 59))
    assert spy.count == 1


async def _all_schedules(session, family_id):
    from sqlalchemy import select
    rows = await session.execute(
        select(DigestSchedule).where(DigestSchedule.family_id == family_id)
    )
    return list(rows.scalars().all())
