"""S2c Nutrition — критерии приёмки A1–A6 (ТЗ spec-nutrition §13).

Тесты на сервисном/read слое: домен Telegram-агностичен (принимает резолвнутого
kernel-Viewer + строку таймзоны). Видимость дневника (PERSONAL) проверяется
напрямую; общий каталог (FAMILY) — через семью. A6 (изоляция read / LLM-локализация)
проверяется import-linter (контракт волны A).

LLM в тестах — ТОЛЬКО фейк за интерфейсом NutritionLLM (I7): ни одного живого
вызова Groq из pytest (GAP-6). Инварианты: I1 (абсолют за порцию, сутки в
таймзоне), I2 (пересчёт на 100 г), I5 (analyze не пишет).
"""

from datetime import date, datetime

import pytest

from app.kernel import Viewer
from app.kernel.models import Family, Membership, User

from app.contexts.nutrition.read import macros_by_day, macros_total
from app.contexts.nutrition.services import (
    FamilyRequired,
    FoodService,
    LLMError,
    LLMRateLimitError,
    NutritionItem,
    NutritionResult,
)

TZ = "Europe/Amsterdam"


# ── Фейковые LLM за интерфейсом NutritionLLM (I7, GAP-6) ──────────────────────

class FakeLLM:
    """Фейк-провайдер: канонический результат, считает вызовы. Сети нет."""

    def __init__(self, result: NutritionResult | None = None) -> None:
        self.result = result or NutritionResult(
            items=[
                NutritionItem(
                    description="гречка", weight_g=200,
                    kcal=680, protein=25, fat=6, carbs=140,
                ),
                NutritionItem(
                    description="курица", weight_g=150,
                    kcal=240, protein=45, fat=6, carbs=0,
                ),
            ]
        )
        self.calls = 0

    async def parse_text(self, text):
        self.calls += 1
        return self.result

    async def analyze_photo(self, image_bytes, caption="", mime_type="image/jpeg"):
        self.calls += 1
        return self.result

    async def analyze_dish_text(self, description, name, weight_g):
        self.calls += 1
        return NutritionResult(items=[self.result.items[0]])

    async def analyze_dish_photos(self, images_b64, name, weight_g):
        self.calls += 1
        return NutritionResult(items=[self.result.items[0]])

    async def analyze_dish_combined(self, text, images_b64, name, weight_g):
        self.calls += 1
        return NutritionResult(items=[self.result.items[0]])


class RaisingLLM:
    """Фейк, имитирующий сбой провайдера (невалидный ответ / 429) — A5/I6."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def parse_text(self, text):
        raise self.exc

    async def analyze_photo(self, image_bytes, caption="", mime_type="image/jpeg"):
        raise self.exc

    async def analyze_dish_text(self, description, name, weight_g):
        raise self.exc

    async def analyze_dish_photos(self, images_b64, name, weight_g):
        raise self.exc

    async def analyze_dish_combined(self, text, images_b64, name, weight_g):
        raise self.exc


# ── Фикстуры семьи/зрителей ───────────────────────────────────────────────────

async def _family(session):
    u1 = User(telegram_user_id=1, first_name="A", timezone=TZ)
    u2 = User(telegram_user_id=2, first_name="B", timezone=TZ)
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


def _svc(session, llm=None) -> FoodService:
    return FoodService(session, llm=llm or FakeLLM())


# ── A1: дневник / 3 способа ввода / цели / выгрузка под kernel ────────────────

async def test_text_input_analyze_then_save(session):
    """A1+A4: текст → analyze (LLM-фейк) → save_entries; запись принадлежит владельцу."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)

    result = await svc.analyze_text("гречка 200, курица 150")
    assert len(result.items) == 2  # одна строка → несколько позиций

    saved = await svc.save_entries(
        v1, result.items, source="manual",
        meal_type="lunch", timezone_name=TZ,
    )
    assert len(saved) == 2
    day = await svc.get_day(v1, date.today(), timezone_name=TZ)
    # I1: сумма за день = SUM абсолютов за порцию
    assert round(day.totals.kcal, 1) == 920.0
    assert round(day.totals.protein, 1) == 70.0


async def test_photo_input_analyze(session):
    """A1: ввод по фото — vision-фейк возвращает позиции (без записи на analyze, I5)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    result = await svc.analyze_photo(b"\xff\xd8jpegbytes", caption="тарелка")
    assert result.items and result.items[0].kcal > 0


async def test_db_input_recalculates_per_portion(session):
    """A1+A4+I2: ввод из каталога — пересчёт КБЖУ по весу порции, без LLM."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    # блюдо 200 г / 680 ккал → 340 ккал на 100 г
    sf = await svc.save_shared_food(
        v1, name="гречка", weight_g=200,
        kcal=680, protein=25, fat=6, carbs=140,
    )
    assert round(sf.kcal_per_100g, 1) == 340.0  # I2: каталог хранит на 100 г

    # порция 100 г → ровно значения на 100 г
    entry = await svc.create_entry_from_shared(v1, sf.id, weight_g=100, meal_type="dinner")
    assert entry is not None
    assert round(entry.kcal, 1) == 340.0
    assert entry.source == "db" and entry.shared_food_id == sf.id


async def test_goals_roundtrip_and_progress(session):
    """A2: цели в NutritionGoals (по user_id); прогресс-бары читают оттуда."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)

    assert (await svc.get_goals(v1)).kcal is None  # цель не задана
    await svc.set_goals(v1, kcal=2000, protein=120, fat=60, carbs=200)
    goals = await svc.get_goals(v1)
    assert goals.kcal == 2000 and goals.protein == 120

    saved = await svc.save_entries(
        v1,
        [NutritionItem(description="омлет", weight_g=150, kcal=300, protein=20, fat=22, carbs=2)],
        source="manual", meal_type="breakfast", timezone_name=TZ,
    )
    assert saved
    day = await svc.get_day(v1, date.today(), timezone_name=TZ)
    prog = day.progress()
    assert prog["kcal"].target == 2000
    assert prog["kcal"].consumed == 300
    assert prog["kcal"].remaining == 1700.0

    # null = снять цель
    await svc.set_goals(v1, kcal=None, protein=None, fat=None, carbs=None)
    assert (await svc.get_goals(v1)).kcal is None


async def test_export_markdown_dump(session):
    """A1+S6: markdown-выгрузка периода с итогами по дням."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    await svc.save_entries(
        v1,
        [NutritionItem(description="гречка", weight_g=200, kcal=680, protein=25, fat=6, carbs=140)],
        source="manual", meal_type="lunch", timezone_name=TZ,
    )
    today = date.today()
    md = await svc.export_period(v1, today, today, timezone_name=TZ)
    assert "# Дневник питания" in md
    assert "гречка" in md and "Итог за период" in md


# ── A3: видимость дневника (PERSONAL) и каталога (FAMILY) ─────────────────────

async def test_diary_personal_invisible_to_partner(session):
    """A3+I4: FoodEntry PERSONAL — партнёр чужой дневник не видит."""
    _, _, _, v1, v2 = await _family(session)
    svc = _svc(session)
    await svc.save_entries(
        v1,
        [NutritionItem(description="секретный ужин", weight_g=300, kcal=500, protein=30, fat=20, carbs=40)],
        source="manual", meal_type="dinner", timezone_name=TZ,
    )
    # владелец видит свой день
    own = await svc.get_day(v1, date.today(), timezone_name=TZ)
    assert len(own.entries) == 1
    # партнёр своего дня — пусто (чужой дневник не виден)
    other = await svc.get_day(v2, date.today(), timezone_name=TZ)
    assert other.entries == []


async def test_partner_cannot_edit_or_delete_foreign_entry(session):
    """A3: правка/удаление чужой записи невозможны (owner-guard)."""
    _, _, _, v1, v2 = await _family(session)
    svc = _svc(session)
    [entry] = await svc.save_entries(
        v1,
        [NutritionItem(description="моё", weight_g=100, kcal=200, protein=10, fat=5, carbs=20)],
        source="manual", meal_type="lunch", timezone_name=TZ,
    )
    assert await svc.update_entry(v2, entry.id, kcal=1) is None
    assert await svc.delete_entry(v2, entry.id) is False
    # владельцу — можно
    updated = await svc.update_entry(v1, entry.id, kcal=222)
    assert updated is not None and updated.kcal == 222


async def test_catalog_family_visible_to_both(session):
    """A3+I4: SharedFood FAMILY — каталог общий для обоих членов семьи."""
    _, _, _, v1, v2 = await _family(session)
    svc = _svc(session)
    sf = await svc.save_shared_food(
        v1, name="общий салат", weight_g=250,
        kcal=300, protein=10, fat=20, carbs=15,
    )
    # партнёр находит блюдо в каталоге семьи
    found = await svc.search_shared_foods(v2, "салат")
    assert [f.id for f in found] == [sf.id]
    # и может добавить себе запись из общего каталога
    entry = await svc.create_entry_from_shared(v2, sf.id, weight_g=250, meal_type="lunch")
    assert entry is not None and entry.user_id == v2.id


async def test_catalog_not_visible_across_families(session):
    """A3: каталог одной семьи не виден другой (FAMILY-изоляция)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    await svc.save_shared_food(
        v1, name="наше блюдо", weight_g=100,
        kcal=100, protein=5, fat=2, carbs=10,
    )
    # отдельный пользователь в другой семье
    u3 = User(telegram_user_id=3, first_name="C", timezone=TZ)
    session.add(u3)
    await session.flush()
    fam2 = Family(created_by=u3.id)
    session.add(fam2)
    await session.flush()
    session.add(Membership(user_id=u3.id, family_id=fam2.id))
    await session.flush()
    v3 = Viewer(id=u3.id, family_id=fam2.id)

    assert await svc.search_shared_foods(v3, "блюдо") == []


async def test_catalog_requires_family(session):
    """A3: каталог FAMILY недоступен без семьи (виртуальный standalone-зритель)."""
    u1, _, _, _, _ = await _family(session)
    svc = _svc(session)
    lonely = Viewer(id=u1.id, family_id=None)
    with pytest.raises(FamilyRequired):
        await svc.save_shared_food(
            lonely, name="x", weight_g=100, kcal=1, protein=1, fat=1, carbs=1
        )


# ── A4 / I5: analyze ≠ save ──────────────────────────────────────────────────

async def test_analyze_does_not_write(session):
    """I5: analyze_* только считают, в БД ничего не пишут."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    await svc.analyze_text("гречка 200")
    await svc.analyze_photo(b"\xff\xd8x", caption="")
    # дневник пуст — analyze не сохраняет
    day = await svc.get_day(v1, date.today(), timezone_name=TZ)
    assert day.entries == []


async def test_save_to_db_creates_shared_food(session):
    """A4: save_entries с save_to_db → запись + блюдо в каталог (на 100 г, I2)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    saved = await svc.save_entries(
        v1,
        [NutritionItem(description="плов", weight_g=300, kcal=600, protein=20, fat=24, carbs=75)],
        source="manual", meal_type="dinner", timezone_name=TZ,
        save_to_db=[True],
    )
    assert saved[0].shared_food_id is not None
    found = await svc.search_shared_foods(v1, "плов")
    assert found and round(found[0].kcal_per_100g, 1) == 200.0  # 600/3


# ── A5: LLM-контракт — провайдер-агностичность + ошибки ──────────────────────

async def test_llm_error_propagates_invalid(session):
    """A5/I6: невалидный ответ модели → LLMError (понятное сообщение)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session, llm=RaisingLLM(LLMError("Модель вернула некорректные данные")))
    with pytest.raises(LLMError):
        await svc.analyze_text("что-то")


async def test_llm_rate_limit_propagates(session):
    """A5/I6: 429 → LLMRateLimitError (подкласс LLMError)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session, llm=RaisingLLM(LLMRateLimitError("Лимит исчерпан")))
    with pytest.raises(LLMError):
        await svc.analyze_photo(b"\xff\xd8x")


async def test_llm_is_injectable_provider_agnostic(session):
    """A5/I7: FoodService зависит лишь от контракта NutritionLLM — фейк подменяет Groq."""
    _, _, _, v1, _ = await _family(session)
    fake = FakeLLM()
    svc = FoodService(session, llm=fake)
    await svc.analyze_text("гречка")
    await svc.analyze_dish_combined("состав", ["b64"], "блюдо", 200)
    assert fake.calls == 2  # вызовы ушли в фейк, не в сеть


# ── A6 / read-модуль: КБЖУ по дням для Analytics (visibility-фильтр) ──────────

async def test_read_macros_by_day_owner_only(session):
    """A6: read.macros_by_day отдаёт КБЖУ владельца по дням; чужой дневник не виден."""
    _, _, _, v1, v2 = await _family(session)
    svc = _svc(session)
    await svc.save_entries(
        v1,
        [NutritionItem(description="завтрак", weight_g=200, kcal=400, protein=20, fat=10, carbs=50)],
        source="manual", meal_type="breakfast", timezone_name=TZ,
    )
    today = date.today()
    days = await macros_by_day(session, v1, today, today, timezone_name=TZ)
    assert len(days) == 1 and days[0]["kcal"] == 400.0

    # партнёр через read-модуль чужой дневник не видит (PERSONAL)
    days_v2 = await macros_by_day(session, v2, today, today, timezone_name=TZ)
    assert days_v2 == []


async def test_read_macros_total_sums_period(session):
    """A6+I1: суммарные КБЖУ за период = SUM абсолютов за порцию."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    await svc.save_entries(
        v1,
        [
            NutritionItem(description="a", weight_g=100, kcal=100, protein=5, fat=2, carbs=10),
            NutritionItem(description="b", weight_g=100, kcal=250, protein=15, fat=8, carbs=20),
        ],
        source="manual", meal_type="lunch", timezone_name=TZ,
    )
    today = date.today()
    total = await macros_total(session, v1, today, today, timezone_name=TZ)
    assert total["kcal"] == 350.0 and total["days_with_data"] == 1


# ── I1: границы суток считаются в таймзоне пользователя ───────────────────────

async def test_day_boundary_in_user_timezone(session):
    """I1: запись, попадающая в другой локальный день из-за таймзоны, относится к нему.

    Берём запись с created_at = 22:30 UTC. В Europe/Amsterdam (UTC+1/+2) это уже
    следующий день после полуночи лишь при большом сдвиге; используем Pacific/Kiritimati
    (UTC+14), где 22:30 UTC 1-го числа — это 12:30 2-го числа локально.
    """
    u = User(telegram_user_id=10, first_name="TZ", timezone="Pacific/Kiritimati")
    session.add(u)
    await session.flush()
    fam = Family(created_by=u.id)
    session.add(fam)
    await session.flush()
    session.add(Membership(user_id=u.id, family_id=fam.id))
    await session.flush()
    v = Viewer(id=u.id, family_id=fam.id)
    svc = _svc(session)

    # 2026-01-01 22:30 UTC → локально 2026-01-02 12:30 (UTC+14)
    await svc.save_entries(
        v,
        [NutritionItem(description="ночной перекус", weight_g=50, kcal=120, protein=2, fat=5, carbs=15)],
        source="manual", meal_type="snack",
        timezone_name="Pacific/Kiritimati",
        created_at=datetime(2026, 1, 1, 22, 30, 0),
    )
    # в локальный день 1-го числа записи нет
    d1 = await svc.get_day(v, date(2026, 1, 1), timezone_name="Pacific/Kiritimati")
    assert d1.entries == []
    # а во 2-м — есть
    d2 = await svc.get_day(v, date(2026, 1, 2), timezone_name="Pacific/Kiritimati")
    assert len(d2.entries) == 1 and d2.totals.kcal == 120
