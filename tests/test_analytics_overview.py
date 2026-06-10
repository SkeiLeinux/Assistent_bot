"""S3 Analytics — кросс-сферный обзор через read-модули, видимость (A1, A4).

A1: обзор (списки/финансы/питание) считается on-the-fly через contexts.*.read,
    visibility-фильтровано.
A4: скрытые элементы и чужое питание не попадают в обзор получателя.

Данные сеются напрямую в моделях доменов (это setup-харнесс, не рантайм Analytics);
сама композиция Analytics ходит ТОЛЬКО в read-модули (изоляция A5 — import-linter).
"""

from datetime import date, datetime
from decimal import Decimal

import app.analytics.schedule  # noqa: F401 — регистрирует digest_schedules в metadata

from app.kernel.models import Family, Membership, User
from app.kernel.sharing import Viewer

from app.contexts.lists.models import ItemStatus, List, ListItem, ListType
from app.contexts.lists.models.enums import ListType as LT  # alias for clarity
from app.contexts.finance.models import ActualAmount, Category
from app.contexts.finance.models.enums import CategoryStatus
from app.contexts.nutrition.models import FoodEntry, NutritionGoals

from app.analytics.read_composition import build_overview


async def _family(session):
    u1 = User(telegram_user_id=101, first_name="Катя", timezone="Europe/Amsterdam")
    u2 = User(telegram_user_id=102, first_name="Сережа", timezone="Europe/Amsterdam")
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


async def _seed_lists(session, fam, owner):
    lst = List(type=ListType.TASK, name="Дела", owner_id=owner.id,
               family_id=fam.id)
    session.add(lst)
    await session.flush()
    # created_at задаём явно (детерминизм: «добавлено» считается по created_at в периоде,
    # не по реальным часам вставки).
    done = ListItem(
        list_id=lst.id, added_by=owner.id, text="готово",
        status=ItemStatus.DONE, created_at=datetime(2026, 6, 9, 8, 0),
        completed_at=datetime(2026, 6, 10, 9, 0),
    )
    open_item = ListItem(
        list_id=lst.id, added_by=owner.id, text="ещё нет", status=ItemStatus.OPEN,
        created_at=datetime(2026, 6, 9, 8, 30),
    )
    session.add_all([done, open_item])
    await session.flush()
    return lst, done, open_item


async def _seed_finance(session, fam):
    parent = Category(name="Едовое", family_id=fam.id, status=CategoryStatus.ACTIVE)
    session.add(parent)
    await session.flush()
    child = Category(name="Продукты", parent_id=parent.id, family_id=fam.id,
                     status=CategoryStatus.ACTIVE)
    session.add(child)
    await session.flush()
    session.add(ActualAmount(category_id=child.id, date=date(2026, 6, 10),
                             amount=Decimal("1500.00")))
    await session.flush()
    return parent, child


async def _seed_food(session, user, kcal):
    session.add(FoodEntry(
        user_id=user.id, created_at=datetime(2026, 6, 10, 12, 0),
        description="обед", kcal=kcal, protein=30, fat=20, carbs=80,
    ))
    await session.flush()


# ── A1: обзор собирается через read; все три сферы присутствуют ────────────────

async def test_overview_aggregates_three_spheres(session):
    fam, u1, u2 = await _family(session)
    await _seed_lists(session, fam, u1)
    await _seed_finance(session, fam)
    await _seed_food(session, u1, kcal=600)
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    ov = await build_overview(
        session, viewer, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )

    # Списки: разрез по типам через recent_changes — added/done за период.
    # TASK: 2 добавлено (оба создано 2026-06-09), 1 выполнено.
    task_line = next(t for t in ov["lists"]["by_type"] if t["type"] == "task")
    assert task_line["added"] == 2
    assert task_line["done"] == 1

    # Финансы: факт за июнь = 1500 (родитель агрегирует ребёнка).
    assert ov["finance"]["fact"] == 1500.0

    # Питание: КБЖУ владельца за период.
    assert ov["nutrition"]["kcal"] == 600.0
    assert ov["range"]["from"] == "2026-06-01"
    assert ov["range"]["to"] == "2026-06-30"


# ── A4: чужое питание (PERSONAL) не попадает в обзор получателя ────────────────

async def test_overview_excludes_other_users_food(session):
    fam, u1, u2 = await _family(session)
    await _seed_food(session, u1, kcal=600)   # питание u1
    await _seed_food(session, u2, kcal=1234)  # питание u2 (чужое для u1)
    await session.commit()

    viewer1 = Viewer(id=u1.id, family_id=fam.id)
    ov1 = await build_overview(
        session, viewer1, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    # Видит только своё питание (600), не чужое (1234).
    assert ov1["nutrition"]["kcal"] == 600.0

    viewer2 = Viewer(id=u2.id, family_id=fam.id)
    ov2 = await build_overview(
        session, viewer2, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert ov2["nutrition"]["kcal"] == 1234.0


# ── A4: скрытый (hidden_from) элемент списка исключён из обзора получателя ─────

async def test_overview_excludes_hidden_list_item(session):
    fam, u1, u2 = await _family(session)
    lst = List(type=ListType.GIFT, name="Подарки", owner_id=u1.id, family_id=fam.id)
    session.add(lst)
    await session.flush()
    # Подарок для u2: DONE, скрыт от u2 (gift hidden_from=[u2]).
    secret = ListItem(
        list_id=lst.id, added_by=u1.id, text="сюрприз",
        status=ItemStatus.DONE, created_at=datetime(2026, 6, 9, 8, 0),
        completed_at=datetime(2026, 6, 10, 9, 0),
    )
    session.add(secret)
    await session.flush()
    # Скрытие: пишем строку в ассоциативную таблицу напрямую (async-safe, без lazy-load).
    from sqlalchemy import insert
    await session.execute(
        insert(ListItem.hidden_from_table()).values(entity_id=secret.id, user_id=u2.id)
    )
    await session.commit()

    # Зритель u1 (видит): gift в разрезе — added=1, done=1.
    viewer1 = Viewer(id=u1.id, family_id=fam.id)
    ov1 = await build_overview(
        session, viewer1, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    gift1 = next(t for t in ov1["lists"]["by_type"] if t["type"] == "gift")
    assert gift1["added"] == 1
    assert gift1["done"] == 1

    # Зритель u2 (скрыто): у gift нет активности → тип отсутствует в разрезе
    # (recent_changes отдаёт только типы с ненулевой активностью; скрытое исключено).
    viewer2 = Viewer(id=u2.id, family_id=fam.id)
    ov2 = await build_overview(
        session, viewer2, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert all(t["type"] != "gift" for t in ov2["lists"]["by_type"])


# ── Q-an-2: цель КБЖУ в обзоре (факт vs цель), через nutrition.read.goals ──────

async def test_overview_nutrition_goal_present(session):
    """Обзор несёт цель КБЖУ владельца (PERSONAL по viewer.id), факт vs цель."""
    fam, u1, u2 = await _family(session)
    await _seed_food(session, u1, kcal=600)
    session.add(NutritionGoals(
        user_id=u1.id, target_kcal=2000, target_protein=120,
        target_fat=70, target_carbs=250,
    ))
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    ov = await build_overview(
        session, viewer, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert ov["nutrition"]["kcal"] == 600.0
    assert ov["nutrition"]["goal"] == {
        "kcal": 2000.0, "protein": 120.0, "fat": 70.0, "carbs": 250.0,
    }


async def test_overview_nutrition_goal_none_when_absent(session):
    """Нет строки целей → goal=None (поведение как раньше: только факт)."""
    fam, u1, u2 = await _family(session)
    await _seed_food(session, u1, kcal=600)
    await session.commit()

    viewer = Viewer(id=u1.id, family_id=fam.id)
    ov = await build_overview(
        session, viewer, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert ov["nutrition"]["goal"] is None


async def test_overview_nutrition_goal_personal_not_partner(session):
    """Цель PERSONAL: получатель видит свою цель, не партнёрскую (A4/I4)."""
    fam, u1, u2 = await _family(session)
    await _seed_food(session, u2, kcal=600)
    session.add(NutritionGoals(
        user_id=u2.id, target_kcal=1800, target_protein=None,
        target_fat=None, target_carbs=None,
    ))
    await session.commit()

    # u1 без своих целей → goal=None (чужая цель u2 не подтягивается).
    v1 = Viewer(id=u1.id, family_id=fam.id)
    ov1 = await build_overview(
        session, v1, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert ov1["nutrition"]["goal"] is None

    # u2 видит свою цель (частично заполненную: kcal задан, прочее None).
    v2 = Viewer(id=u2.id, family_id=fam.id)
    ov2 = await build_overview(
        session, v2, date(2026, 6, 1), date(2026, 6, 30), tz_name="Europe/Amsterdam"
    )
    assert ov2["nutrition"]["goal"]["kcal"] == 1800.0
    assert ov2["nutrition"]["goal"]["protein"] is None
