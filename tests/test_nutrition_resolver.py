"""R1 retrieval-augmented разбор КБЖУ (ADR-0013 / spec-nutrition-retrieval §14).

Без живых вызовов: извлекатель драйвится фейковым LLM-клиентом (канонический
JSON), OFF — фейковым fetch на реальных JSON-фикстурах (`tests/fixtures/off_*.json`),
лестница — фейковыми источниками. Покрытие: extractor (мульти), refine (label/off/
estimate/таймаут), OFF маппинг+выбор, мульти-ввод, масштаб, кэш, бюджет, контракт.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.kernel import Viewer
from app.kernel.models import Family, Membership, User

from app.contexts.nutrition.resolve import (
    EstimateSource,
    LLMExtractor,
    OpenFoodFactsSource,
    ResolverPipeline,
    prompts,
)
from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.ports import (
    ExtractedItem,
    Macros,
    ResolvedNutrition,
    ResolveQuery,
)
from app.contexts.nutrition.resolve.sources.openfoodfacts import (
    map_product,
    select_best,
)
from app.contexts.nutrition.services import FoodService, NutritionItem

TZ = "Europe/Amsterdam"
_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ── Фейковый LLM-клиент для извлекателя (живых вызовов нет) ───────────────────

class _FakeCreate:
    """Подмена chat.completions.create: возвращает канонический content, ловит kwargs."""

    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.kwargs: dict | None = None
        self.calls = 0

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        self.calls += 1
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason=self.finish_reason)],
            usage=None,
        )


def _patch_extractor(monkeypatch, content: str) -> _FakeCreate:
    """Клиент патчится в llm_service: транспорт теперь один на весь домен (§5.3)."""
    import app.contexts.nutrition.services.llm_service as llm_mod

    create = _FakeCreate(content)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_mod, "_get_client", lambda: client)
    return create


# ── Фейковые источники/резолвер лестницы ──────────────────────────────────────

class FakeSource:
    """Ступень-источник с заданным поведением (hit / miss / сбой). Считает вызовы."""

    def __init__(self, name="fake", result=None, exc=None, timeout_s=2.5):
        self.name = name
        self.result = result
        self.exc = exc
        self.timeout_s = timeout_s
        self.calls = 0
        self.queries: list[ResolveQuery] = []

    async def lookup(self, q: ResolveQuery):
        self.calls += 1
        self.queries.append(q)
        if self.exc is not None:
            raise self.exc
        return self.result


class RecordingResolver:
    """Фейк-резолвер: фиксирует allow_network на каждой позиции (бюджет, §3)."""

    def __init__(self, per_item_source: dict | None = None, total_budget_s=12.0):
        self.total_budget_s = total_budget_s
        self.allow_flags: list[bool] = []
        self.per_item_source = per_item_source or {}

    async def refine(self, item: ExtractedItem, *, allow_network: bool = True):
        self.allow_flags.append(allow_network)
        src = self.per_item_source.get(item.name, "estimate")
        return ResolvedNutrition(per100g=item.per100g, source=src)


def _macros(kcal=100.0, protein=5.0, fat=2.0, carbs=10.0) -> Macros:
    return Macros(kcal=kcal, protein=protein, fat=fat, carbs=carbs)


def _extracted(name="гречка", portion_g=200.0, readout="estimate", **kw) -> ExtractedItem:
    return ExtractedItem(
        name=name, portion_g=portion_g, per100g=_macros(**kw), readout=readout,
    )


def _pipeline(network_sources) -> ResolverPipeline:
    return ResolverPipeline(
        network_sources=network_sources, fallback=EstimateSource(), total_budget_s=12.0
    )


# ════════════════════════════ EXTRACTOR (§6) ═════════════════════════════════

async def test_extractor_text_two_items(monkeypatch):
    """«гречка 200, курица 150» → 2 ExtractedItem с per100g и portion_g (регресс)."""
    content = json.dumps(
        {
            "items": [
                {"name": "гречка", "portion_g": 200, "readout": "estimate",
                 "per100g": {"kcal": 340, "protein": 13, "fat": 3, "carbs": 70}},
                {"name": "курица", "portion_g": 150, "readout": "estimate",
                 "per100g": {"kcal": 165, "protein": 31, "fat": 3.6, "carbs": 0}},
            ]
        }
    )
    create = _patch_extractor(monkeypatch, content)
    items = await LLMExtractor().extract(text="гречка 200, курица 150")

    assert len(items) == 2
    assert items[0].name == "гречка" and items[0].portion_g == 200
    assert items[0].per100g.kcal == 340
    assert items[1].name == "курица" and items[1].portion_g == 150
    # системный промпт — один на все пять путей (ТЗ §4.1); режим задаёт user-часть
    assert create.kwargs["messages"][0]["content"] == prompts.SYSTEM_PROMPT_V2
    assert create.kwargs["messages"][1]["content"] == "гречка 200, курица 150"


async def test_extractor_single_with_brand_and_fat(monkeypatch):
    """«200гр молоко Домик в деревне 3.2%» → 1 item с brand/fat_pct/portion."""
    content = json.dumps(
        {"items": [{"name": "молоко питьевое", "brand": "Домик в деревне",
                    "fat_pct": 3.2, "portion_g": 200, "readout": "estimate",
                    "per100g": {"kcal": 60, "protein": 3, "fat": 3.2, "carbs": 4.7}}]}
    )
    _patch_extractor(monkeypatch, content)
    items = await LLMExtractor().extract(text="200гр молоко Домик в деревне 3.2%")

    assert len(items) == 1
    it = items[0]
    assert it.brand == "Домик в деревне" and it.fat_pct == 3.2
    assert it.portion_g == 200


async def test_extractor_photo_readout_label(monkeypatch):
    """Фото этикетки → одна запись readout="label" (per100g списаны с таблицы)."""
    content = json.dumps(
        {"items": [{"name": "йогурт", "readout": "label", "portion_g": 125,
                    "per100g": {"kcal": 90, "protein": 4, "fat": 3, "carbs": 12}}]}
    )
    create = _patch_extractor(monkeypatch, content)
    items = await LLMExtractor().extract(images=[b"\xff\xd8jpeg"], caption="этикетка")

    assert len(items) == 1 and items[0].readout == "label"
    # vision-вход: текст + image-часть, один вызов
    content_parts = create.kwargs["messages"][1]["content"]
    assert any(p["type"] == "image_url" for p in content_parts)


async def test_extractor_photo_plate_multi_estimate(monkeypatch):
    """Фото тарелки → N записей readout="estimate" (оценка)."""
    content = json.dumps(
        {"items": [
            {"name": "рис", "readout": "estimate", "portion_g": 150,
             "per100g": {"kcal": 130, "protein": 2.7, "fat": 0.3, "carbs": 28}},
            {"name": "котлета", "readout": "estimate", "portion_g": 100,
             "per100g": {"kcal": 250, "protein": 18, "fat": 18, "carbs": 5}},
        ]}
    )
    _patch_extractor(monkeypatch, content)
    items = await LLMExtractor().extract(images=[b"a", b"b"])
    assert len(items) == 2 and all(it.readout == "estimate" for it in items)


async def test_extractor_accepts_legacy_label_complete(monkeypatch):
    """Промпт v1 отвечает `label_complete` — раннер обязан уметь прогнать обе версии.

    Совместимость нужна ровно для сравнения версий промпта (ТЗ §12.3): без неё
    baseline-прогон падал бы на схеме, и утверждение «не хуже baseline» стало бы
    непроверяемым.
    """
    content = json.dumps(
        {"items": [{"name": "йогурт", "label_complete": True, "portion_g": 125,
                    "per100g": {"kcal": 90, "protein": 4, "fat": 3, "carbs": 12}}]}
    )
    create = _patch_extractor(monkeypatch, content)
    items = await LLMExtractor(prompt_version="v1").extract(images=[b"x"])

    assert items[0].readout == "label"
    assert create.kwargs["messages"][0]["content"] == prompts.SYSTEM_PROMPT_V1


# ════════════════════════ REFINE — ЛЕСТНИЦА (§4) ════════════════════════════

async def test_refine_label_without_brand_skips_network():
    """readout=label БЕЗ бренда → source=label, сеть не трогаем (§7.2).

    Прежнее правило коротило лестницу на любой этикетке; теперь короткое
    замыкание осталось только там, где сверять не с чем: без бренда шанс найти
    товар в источнике близок к нулю, а запрос стоит времени (§7.3).
    """
    off = FakeSource(name="off", result=ResolvedNutrition(per100g=_macros(), source="off"))
    pipe = _pipeline([off])
    item = _extracted(readout="label", kcal=90)
    r = await pipe.refine(item)
    assert r.source == "label" and r.per100g.kcal == 90
    assert off.calls == 0  # сверять не с чем


async def test_refine_label_with_brand_queries_source_but_keeps_label():
    """readout=label + бренд → источник спрашивается, но числа этикетки остаются.

    Гейт §7.1a (блок 10) сейчас закрыт — `match_confidence` ступень не сообщает,
    поэтому расхождение НЕ показывается: при матчинге 36% оно было бы ложным в
    двух случаях из трёх. Числа не подменяются в любом случае (§2.4).
    """
    off = FakeSource(
        name="off", result=ResolvedNutrition(per100g=_macros(kcal=500), source="off")
    )
    item = _extracted(readout="label", kcal=90, name="молоко")
    item = item.model_copy(update={"brand": "Домик в деревне"})
    r = await _pipeline([off]).refine(item)

    assert off.calls == 1                       # сверка запрошена
    assert r.source == "label" and r.per100g.kcal == 90   # числа не подменены
    assert "label_disagrees_with_source" not in r.warnings  # гейт закрыт


async def test_refine_label_downgraded_by_checks_goes_to_ladder():
    """Проваленные проверки понизили readout → обычный проход лестницы (§6.2).

    Это и есть маршрутная роль проверок: они не отклоняют ответ, а отправляют
    позицию искать числа во внешнем источнике.
    """
    from app.contexts.nutrition.resolve.checks import apply_checks

    off = FakeSource(
        name="off", result=ResolvedNutrition(per100g=_macros(kcal=60), source="off")
    )
    # kcal 900 при БЖУ 5/2/10 — Атуотер даёт 78, расхождение больше 20%.
    item = apply_checks(_extracted(readout="label", kcal=900))
    assert item.readout == "estimate"

    r = await _pipeline([off]).refine(item)
    assert r.source == "off" and off.calls == 1


async def test_refine_off_hit():
    """OFF нашёл → source=off, его per100g."""
    hit = ResolvedNutrition(per100g=_macros(kcal=60), source="off", source_ref="u")
    off = FakeSource(name="off", result=hit)
    r = await _pipeline([off]).refine(_extracted())
    assert r.source == "off" and r.per100g.kcal == 60 and off.calls == 1


async def test_refine_off_miss_falls_to_estimate():
    """OFF miss (None) → estimate = per100g из item."""
    off = FakeSource(name="off", result=None)
    item = _extracted(kcal=333)
    r = await _pipeline([off]).refine(item)
    assert r.source == "estimate" and r.per100g.kcal == 333 and off.calls == 1


async def test_refine_off_timeout_falls_to_estimate():
    """Таймаут OFF → estimate (деградация, не краш)."""
    off = FakeSource(name="off", exc=TimeoutError())
    r = await _pipeline([off]).refine(_extracted(kcal=222))
    assert r.source == "estimate" and r.per100g.kcal == 222


async def test_refine_source_error_falls_to_estimate():
    """SourceError ступени → estimate (следующая/в пределе оценка)."""
    off = FakeSource(name="off", exc=SourceError("HTTP 503"))
    r = await _pipeline([off]).refine(_extracted(kcal=111))
    assert r.source == "estimate" and r.per100g.kcal == 111


async def test_refine_skip_network_when_budget_exhausted():
    """allow_network=False → estimate, источник не вызывается (бюджет вышел)."""
    off = FakeSource(name="off", result=ResolvedNutrition(per100g=_macros(), source="off"))
    r = await _pipeline([off]).refine(_extracted(kcal=99), allow_network=False)
    assert r.source == "estimate" and off.calls == 0


# ════════════════════════ OFF МАППИНГ / ВЫБОР (§7) ══════════════════════════

def test_off_map_product_kcal_direct():
    """energy-kcal_100g → kcal; остальные поля 1:1 (фикстура «Домик в деревне»)."""
    milk = _fixture("off_milk_search.json")["products"][0]
    m = map_product(milk)
    assert m is not None
    assert m.kcal == 60 and m.protein == 3 and m.fat == 3.2 and m.carbs == 4.7


def test_off_map_product_kj_fallback():
    """Нет energy-kcal_100g → пересчёт из energy-kj_100g / 4.184."""
    product = {"nutriments": {"energy-kj_100g": 1787, "proteins_100g": 14,
                              "fat_100g": 11, "carbohydrates_100g": 68}}
    m = map_product(product)
    assert m is not None and m.kcal == round(1787 / 4.184, 1)  # ≈ 427.1


def test_off_map_product_missing_macro_skips():
    """Нет белка → продукт невалиден (None, skip)."""
    product = {"nutriments": {"energy-kcal_100g": 100, "fat_100g": 5,
                              "carbohydrates_100g": 10}}  # без proteins_100g
    assert map_product(product) is None


def test_off_select_best_prefers_brand_and_completeness():
    """Выбор: полный + бренд + близкая жирность + completeness → топ-1."""
    products = _fixture("off_milk_search.json")["products"]
    q = ResolveQuery(name="молоко питьевое", brand="Домик в деревне", fat_pct=3.2,
                     fallback_per100g=_macros())
    best = select_best(products, q)
    assert best is not None and best["code"] == "4690228004018"


def test_off_select_best_empty_is_none():
    products = _fixture("off_empty_search.json")["products"]
    q = ResolveQuery(name="несуществующее", fallback_per100g=_macros())
    assert select_best(products, q) is None


async def test_off_source_lookup_with_fixture_fetch():
    """OFF.lookup на фикстуре (fake fetch) → ResolvedNutrition(source=off)."""
    captured = {}

    async def fake_fetch(params):
        captured["params"] = params
        return _fixture("off_milk_search.json")

    src = OpenFoodFactsSource(
        base_url="https://world.openfoodfacts.org",
        user_agent="test/1.0 (t@e.x)", fetch=fake_fetch,
    )
    q = ResolveQuery(name="молоко питьевое", brand="Домик в деревне", fat_pct=3.2,
                     fallback_per100g=_macros())
    r = await src.lookup(q)
    assert r is not None and r.source == "off"
    assert r.per100g.kcal == 60
    assert "4690228004018" in (r.source_ref or "")
    assert r.matched_name
    # search_terms собрал бренд+имя+жирность
    assert "Домик в деревне" in captured["params"]["search_terms"]
    assert "3.2%" in captured["params"]["search_terms"]


async def test_off_source_lookup_empty_returns_none():
    async def fake_fetch(params):
        return _fixture("off_empty_search.json")

    src = OpenFoodFactsSource(base_url="https://x", user_agent="t", fetch=fake_fetch)
    assert await src.lookup(ResolveQuery(name="x", fallback_per100g=_macros())) is None


async def test_off_source_http_error_raises_then_pipeline_estimates():
    """HTTP-сбой fetch → SourceError; в лестнице это деградирует в estimate."""
    async def boom(params):
        raise SourceError("OFF HTTP 503")

    src = OpenFoodFactsSource(base_url="https://x", user_agent="t", fetch=boom)
    r = await _pipeline([src]).refine(_extracted(kcal=777))
    assert r.source == "estimate" and r.per100g.kcal == 777


# ════════════════════ МУЛЬТИ-ВВОД / МАСШТАБ / БЮДЖЕТ (через FoodService) ═════

class FakeExtractor:
    """Возвращает заранее заданный список ExtractedItem (без LLM)."""

    def __init__(self, items: list[ExtractedItem]):
        self.items = items
        self.calls = 0

    async def extract(self, *, mode="diary", text=None, images=None, caption="",
                      mime_type="image/jpeg", dish_name=None, weight_g=None):
        self.calls += 1
        return self.items


def _svc(session, *, extractor, resolver):
    return FoodService(session, extractor=extractor, resolver=resolver)


async def test_multi_input_mixed_sources_order_preserved(session):
    """Список из 2: одна off, другая estimate; порядок и число сохранены."""
    ext = FakeExtractor([_extracted(name="молоко", kcal=60), _extracted(name="гречка", kcal=340)])
    resolver = RecordingResolver(per_item_source={"молоко": "off", "гречка": "estimate"})
    svc = _svc(session, extractor=ext, resolver=resolver)

    result = await svc.analyze_text("молоко 100, гречка 200")
    assert [it.source for it in result.items] == ["off", "estimate"]
    assert len(result.items) == 2


async def test_scale_per_portion(session):
    """per100g × portion/100 на каждую позицию (kcal=60/100г, порция 200 → 120)."""
    ext = FakeExtractor([_extracted(name="молоко", portion_g=200, kcal=60, protein=3, fat=3.2, carbs=4.7)])
    resolver = RecordingResolver(per_item_source={"молоко": "off"})
    svc = _svc(session, extractor=ext, resolver=resolver)

    result = await svc.analyze_text("молоко 200")
    it = result.items[0]
    assert it.weight_g == 200 and it.kcal == 120.0 and it.protein == 6.0
    # опция C: per100g протащен (база для пересчёта на confirm при правке веса)
    assert it.kcal_100g == 60 and it.protein_100g == 3


async def test_scale_portion_none_uses_per100g(session):
    """portion_g=None → числа = per100g, вес неизвестен (поведение как раньше)."""
    ext = FakeExtractor([_extracted(name="яблоко", portion_g=None, kcal=52, protein=0.3, fat=0.2, carbs=14)])
    resolver = RecordingResolver(per_item_source={"яблоко": "estimate"})
    svc = _svc(session, extractor=ext, resolver=resolver)

    it = (await svc.analyze_text("яблоко")).items[0]
    assert it.weight_g is None and it.kcal == 52.0


async def test_budget_exhausted_skips_network_for_remainder(session):
    """total_budget_s=0 → все позиции резолвятся без сети (allow_network=False)."""
    ext = FakeExtractor([_extracted(name="a"), _extracted(name="b"), _extracted(name="c")])
    resolver = RecordingResolver(total_budget_s=0.0)
    svc = _svc(session, extractor=ext, resolver=resolver)

    await svc.analyze_text("a, b, c")
    assert resolver.allow_flags == [False, False, False]


async def test_budget_generous_allows_network(session):
    """Достаточный бюджет → сетевые ступени разрешены (allow_network=True)."""
    ext = FakeExtractor([_extracted(name="a"), _extracted(name="b")])
    resolver = RecordingResolver(total_budget_s=60.0)
    svc = _svc(session, extractor=ext, resolver=resolver)

    await svc.analyze_text("a, b")
    assert resolver.allow_flags == [True, True]


# ════════════════════════════════ КЭШ (§11) ═════════════════════════════════

async def _family(session):
    u1 = User(telegram_user_id=1, first_name="A", timezone=TZ)
    u2 = User(telegram_user_id=2, first_name="B", timezone=TZ)
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
    return u1, fam, Viewer(id=u1.id, family_id=fam.id)


def _cache_svc(session):
    # для save_entries extractor/resolver не нужны (вызывается напрямую с items)
    return FoodService(session, extractor=FakeExtractor([]), resolver=RecordingResolver())


def _item(name="молоко Домик в деревне 3.2%", source="off", **kw):
    return NutritionItem(
        description=name, weight_g=kw.get("weight_g", 200),
        kcal=kw.get("kcal", 120), protein=kw.get("protein", 6),
        fat=kw.get("fat", 6.4), carbs=kw.get("carbs", 9.4),
        source=source, source_ref=kw.get("source_ref"),
    )


async def test_cache_off_creates_shared_food(session):
    """save с source=off → SharedFood создан (FAMILY), запись слинкована и off."""
    _, fam, v1 = await _family(session)
    svc = _cache_svc(session)
    [entry] = await svc.save_entries(v1, [_item(source="off", source_ref="u")],
                                     source="manual", meal_type="lunch", timezone_name=TZ)
    assert entry.source == "off" and entry.source_ref == "u"
    assert entry.shared_food_id is not None
    found = await svc.search_shared_foods(v1, "молоко")
    assert len(found) == 1 and round(found[0].kcal_per_100g, 1) == 60.0  # 120/2


async def test_cache_dedup_no_duplicate(session):
    """Повтор того же ключа → без дубля (use_count++)."""
    _, fam, v1 = await _family(session)
    svc = _cache_svc(session)
    await svc.save_entries(v1, [_item(source="off")], source="manual", timezone_name=TZ)
    await svc.save_entries(v1, [_item(source="off")], source="manual", timezone_name=TZ)
    found = await svc.search_shared_foods(v1, "молоко")
    assert len(found) == 1 and found[0].use_count == 1  # второй раз — инкремент, не дубль


async def test_cache_estimate_not_cached(session):
    """source=estimate → в каталог НЕ кладём."""
    _, fam, v1 = await _family(session)
    svc = _cache_svc(session)
    [entry] = await svc.save_entries(v1, [_item(name="борщ домашний", source="estimate")],
                                     source="manual", timezone_name=TZ)
    assert entry.source == "estimate" and entry.shared_food_id is None
    assert await svc.search_shared_foods(v1, "борщ") == []


async def test_cache_skipped_outside_family_but_entry_written(session):
    """Вне семьи кэш пропускаем; запись в дневник всё равно идёт (§11)."""
    u = User(telegram_user_id=9, first_name="solo", timezone=TZ)
    session.add(u)
    await session.flush()
    lonely = Viewer(id=u.id, family_id=None)
    svc = _cache_svc(session)
    [entry] = await svc.save_entries(lonely, [_item(source="off")], source="manual", timezone_name=TZ)
    assert entry.source == "off" and entry.shared_food_id is None
    day = await svc.get_day(lonely, timezone_name=TZ)
    assert len(day.entries) == 1  # дневник записан


# ════════════════════════════ КОНТРАКТ (§10) ════════════════════════════════

async def test_contract_entry_persists_source_ref(session):
    """/entries: source/source_ref сохраняются на записи (через сервис)."""
    _, fam, v1 = await _family(session)
    svc = _cache_svc(session)
    [entry] = await svc.save_entries(
        v1, [_item(source="off", source_ref="https://off/product/123")],
        source="manual", timezone_name=TZ,
    )
    assert entry.source == "off"
    assert entry.source_ref == "https://off/product/123"
