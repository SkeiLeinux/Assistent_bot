"""S2c Nutrition — критерии приёмки A1–A6 (ТЗ spec-nutrition §13).

Тесты на сервисном/read слое: домен Telegram-агностичен (принимает резолвнутого
kernel-Viewer + строку таймзоны). Видимость дневника (PERSONAL) проверяется
напрямую; общий каталог (FAMILY) — через семью. A6 (изоляция read / LLM-локализация)
проверяется import-linter (контракт волны A).

LLM в тестах — ТОЛЬКО фейк за интерфейсом NutritionLLM (I7): ни одного живого
вызова провайдера из pytest (GAP-6). Инварианты: I1 (абсолют за порцию, сутки в
таймзоне), I2 (пересчёт на 100 г), I5 (analyze не пишет).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.kernel import Viewer
from app.kernel.models import Family, Membership, User

from app.contexts.nutrition.read import goals as read_goals
from app.contexts.nutrition.read import macros_by_day, macros_total
from app.contexts.nutrition.resolve.ports import ExtractedItem, Macros, ResolvedNutrition
from app.contexts.nutrition.services import (
    FamilyRequired,
    FoodService,
    InvalidInput,
    LLMError,
    LLMRateLimitError,
    NutritionItem,
    NutritionResult,
)

TZ = "Europe/Amsterdam"


def _today() -> date:
    """Сегодня В ЗОНЕ ТЕСТА (TZ), а не машины. save_entries без created_at кладёт
    запись на utcnow и бакетит сутки в TZ; запрашивать день нужно той же зоной,
    иначе у машины не-UTC (напр. UTC+3) дата разъезжается с бакетом → 0 (флак)."""
    return datetime.now(ZoneInfo(TZ)).date()


# ── Фейковый извлекатель — единственный LLM-шов сервиса (I7, GAP-6) ───────────
# До волны 1 их было два: `NutritionLLM` для каталога и `Extractor` для дневника,
# в двух несовместимых контрактных моделях (абсолют за порцию vs per100g). После
# консолидации (ТЗ §4.3) шов один, режим — параметр, поэтому фейков тоже один.

# ── Фейковые извлекатель/резолвер дневникового /analyze (ADR-0013, без сети) ───

def _default_extracted() -> list[ExtractedItem]:
    """Канонический разбор «гречка 200, курица 150»: per100g × порция = абсолют.

    340×2=680 ккал / 12.5×2=25 Б (гречка); 160×1.5=240 ккал / 30×1.5=45 Б (курица)
    → совпадает с прежним FakeLLM-результатом (920 ккал / 70 Б суммарно)."""
    return [
        ExtractedItem(name="гречка", portion_g=200,
                      per100g=Macros(kcal=340, protein=12.5, fat=3, carbs=70)),
        ExtractedItem(name="курица", portion_g=150,
                      per100g=Macros(kcal=160, protein=30, fat=4, carbs=0)),
    ]


class FakeExtractor:
    """Возвращает заданный список ExtractedItem (1 «вызов», без LLM)."""

    def __init__(self, items: list[ExtractedItem] | None = None) -> None:
        self.items = items if items is not None else _default_extracted()
        self.calls = 0
        self.last_mode = None
        self.last_weight_g = None

    async def extract(self, *, mode="diary", text=None, images=None, caption="",
                      mime_type="image/jpeg", dish_name=None, weight_g=None):
        self.calls += 1
        self.last_mode = mode
        self.last_weight_g = weight_g
        return self.items


class RaisingExtractor:
    """Извлекатель, имитирующий сбой LLM (невалидный ответ / 429) — A5/I6."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def extract(self, **kwargs):
        raise self.exc


class PassthroughResolver:
    """Резолвер-заглушка: per100g без изменений, source=estimate (сеть не трогаем)."""

    total_budget_s = 12.0

    def __init__(self) -> None:
        self.calls = 0

    async def refine(self, item, *, allow_network: bool = True):
        self.calls += 1
        return ResolvedNutrition(per100g=item.per100g, source="estimate")


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


def _svc(session, extractor=None, resolver=None) -> FoodService:
    return FoodService(
        session,
        extractor=extractor or FakeExtractor(),
        resolver=resolver or PassthroughResolver(),
    )


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
    day = await svc.get_day(v1, _today(), timezone_name=TZ)
    # I1: сумма за день = SUM абсолютов за порцию
    assert round(day.totals.kcal, 1) == 920.0
    assert round(day.totals.protein, 1) == 70.0


async def test_photo_input_analyze(session):
    """A1: ввод по фото — vision-фейк возвращает позиции (без записи на analyze, I5)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session)
    result = await svc.analyze_photo([b"\xff\xd8jpegbytes"], caption="тарелка")
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
    day = await svc.get_day(v1, _today(), timezone_name=TZ)
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
    today = _today()
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
    own = await svc.get_day(v1, _today(), timezone_name=TZ)
    assert len(own.entries) == 1
    # партнёр своего дня — пусто (чужой дневник не виден)
    other = await svc.get_day(v2, _today(), timezone_name=TZ)
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
    await svc.analyze_photo([b"\xff\xd8x"], caption="")
    # дневник пуст — analyze не сохраняет
    day = await svc.get_day(v1, _today(), timezone_name=TZ)
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
    """A5/I6: невалидный ответ модели при извлечении → LLMError (понятное сообщение)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session, extractor=RaisingExtractor(LLMError("Модель вернула некорректные данные")))
    with pytest.raises(LLMError):
        await svc.analyze_text("что-то")


async def test_llm_rate_limit_propagates(session):
    """A5/I6: 429 при извлечении → LLMRateLimitError (подкласс LLMError)."""
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session, extractor=RaisingExtractor(LLMRateLimitError("Лимит исчерпан")))
    with pytest.raises(LLMError):
        await svc.analyze_photo([b"\xff\xd8x"])


async def test_dish_llm_error_propagates(session):
    """A5/I6: каталог (/foods/analyze) — расчётный путь LLM; сбой → LLMError.

    Раньше сбой приходил из отдельного LLM-шва каталога; теперь шов один, но
    свойство прежнее: ошибка провайдера доходит до пользователя понятной, а не
    прикидывается пустым разбором.
    """
    _, _, _, v1, _ = await _family(session)
    svc = _svc(session, extractor=RaisingExtractor(LLMError("Сервис недоступен")))
    with pytest.raises(LLMError):
        await svc.analyze_dish_combined("состав", ["Yg=="], "блюдо", 200)


async def test_llm_is_injectable_provider_agnostic(session):
    """A5/I7: LLM-шов инъектируем и один на все пять путей; вендора сервис не знает.

    Прежняя версия проверяла ДВА шва (извлекатель для дневника, `NutritionLLM`
    для каталога). Проверка не потеряна, а усилена: теперь тот же тест требует,
    чтобы оба пути шли через один и тот же контракт и различались только режимом
    (ТЗ §4.3) — раньше это невозможно было выразить.
    """
    _, _, _, v1, _ = await _family(session)
    ext = FakeExtractor()
    svc = FoodService(session, extractor=ext, resolver=PassthroughResolver())

    await svc.analyze_text("гречка")
    assert ext.last_mode == "diary"
    await svc.analyze_dish_combined("состав", ["Yg=="], "блюдо", 200)
    assert ext.last_mode == "dish_combined" and ext.last_weight_g == 200
    assert ext.calls == 2  # один и тот же шов, два режима


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
    today = _today()
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
    today = _today()
    total = await macros_total(session, v1, today, today, timezone_name=TZ)
    assert total["kcal"] == 350.0 and total["days_with_data"] == 1


async def test_read_goals_projection_owner_only(session):
    """Q-an-2: read.goals отдаёт цели КБЖУ владельца для «факт vs цель»; PERSONAL.

    Цель не задана → None; задана → {"kcal","protein","fat","carbs"}; чужие цели
    не видны (фильтр по viewer.id).
    """
    _, _, _, v1, v2 = await _family(session)
    svc = _svc(session)

    # цели не заведены → проекция отдаёт None
    assert await read_goals(session, v1) is None

    await svc.set_goals(v1, kcal=2000, protein=120, fat=60, carbs=200)
    g = await read_goals(session, v1)
    assert g == {"kcal": 2000.0, "protein": 120.0, "fat": 60.0, "carbs": 200.0}

    # партнёр чужие цели не видит (PERSONAL по viewer.id)
    assert await read_goals(session, v2) is None


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


# ── Составное блюдо: одна запись + состав-снимок (фича 2026-06-14) ────────────

def _ingredients_pair():
    return [
        {"name": "паста", "weight_g": 120, "kcal": 180, "protein": 6, "fat": 2,
         "carbs": 35, "source": "off", "source_ref": "u1"},
        {"name": "соус", "weight_g": 90, "kcal": 210, "protein": 9, "fat": 14,
         "carbs": 8, "source": "estimate", "source_ref": None},
    ]


async def test_composite_dish_one_entry_with_ingredients(session):
    """N распознанных позиций → ОДНА запись (агрегат) + состав-снимок на ней."""
    _, _, _, v, _ = await _family(session)
    svc = _svc(session)
    entry = await svc.save_composite_dish(
        v, name="Паста болоньезе", ingredients=_ingredients_pair(),
        source="manual", meal_type="lunch", timezone_name=TZ, save_to_catalog=False,
    )
    # одна запись = сумма строк (абсолют за порцию, I1)
    assert entry.description == "Паста болоньезе"
    assert entry.weight_g == 210  # 120 + 90
    assert (entry.kcal, entry.protein, entry.fat, entry.carbs) == (390, 15, 16, 43)
    assert entry.shared_food_id is None  # каталог не просили
    # состав висит на записи, провенанс per-ingredient сохранён
    rows = (await svc.ingredients_for_entries([entry.id]))[entry.id]
    assert [r.name for r in rows] == ["паста", "соус"]
    assert (rows[0].source, rows[0].source_ref) == ("off", "u1")
    assert rows[1].source == "estimate"
    assert [r.position for r in rows] == [0, 1]


async def test_composite_dish_to_catalog_creates_shared_food(session):
    """save_to_catalog=True → блюдо каталога (per-100g из агрегата) + свой состав."""
    _, _, _, v, _ = await _family(session)
    svc = _svc(session)
    ingredients = [
        {"name": "рис", "weight_g": 150, "kcal": 200, "protein": 4, "fat": 1,
         "carbs": 45, "source": "off", "source_ref": None},
        {"name": "курица", "weight_g": 100, "kcal": 165, "protein": 31, "fat": 4,
         "carbs": 0, "source": "estimate", "source_ref": None},
    ]
    entry = await svc.save_composite_dish(
        v, name="Рис с курицей", ingredients=ingredients,
        source="manual", meal_type="dinner", timezone_name=TZ, save_to_catalog=True,
    )
    assert entry.shared_food_id is not None
    foods = await svc.search_shared_foods(v, "Рис")
    assert len(foods) == 1
    sf = foods[0]
    assert sf.weight_g == 250  # 150 + 100
    assert round(sf.kcal_per_100g, 1) == 146.0  # 365 / 250 * 100
    # состав висит и на блюде каталога, и на записи
    assert [r.name for r in (await svc.ingredients_for_foods([sf.id]))[sf.id]] == ["рис", "курица"]
    assert len((await svc.ingredients_for_entries([entry.id]))[entry.id]) == 2


async def test_composite_dish_delete_cascades_ingredients(session):
    """Удаление записи уносит её состав (FK ON DELETE CASCADE, pragma ON в conftest)."""
    _, _, _, v, _ = await _family(session)
    svc = _svc(session)
    entry = await svc.save_composite_dish(
        v, name="Блюдо", ingredients=_ingredients_pair(),
        source="manual", timezone_name=TZ, save_to_catalog=False,
    )
    assert (await svc.dish_repo.for_entries([entry.id]))  # состав есть
    assert await svc.delete_entry(v, entry.id) is True
    assert await svc.dish_repo.for_entries([entry.id]) == {}  # каскад унёс


async def test_composite_dish_to_catalog_requires_weight(session):
    """В каталог нельзя без суммарного веса (per-100g не из чего считать)."""
    _, _, _, v, _ = await _family(session)
    svc = _svc(session)
    with pytest.raises(InvalidInput):
        await svc.save_composite_dish(
            v, name="Без веса",
            ingredients=[{"name": "x", "weight_g": None, "kcal": 10, "protein": 0,
                          "fat": 0, "carbs": 0, "source": None, "source_ref": None}],
            source="manual", timezone_name=TZ, save_to_catalog=True,
        )
