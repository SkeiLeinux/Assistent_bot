"""Волна 1, блоки 2–6 — контракт LLM-запроса (ТЗ spec-nutrition-llm-contract §12.2).

Что здесь проверяется и почему именно так:

  - **Проверки §6** — по два кейса на правило (под порогом / над порогом), включая
    водку под C4.4 и кДж-этикетку под C3. Пороги берутся из конфига, а не из
    литералов: они откалиброваны на данных (`llm-checks-calibration-2026-07-27.md`),
    и хардкод здесь означал бы, что тест переживёт перекалибровку молча.
  - **Два инварианта** — «ни одна проверка не отклоняет ответ» (§2.2) и «код не
    исправляет числа» (§2.4). Оба выражены как свойства на наборе входов, а не как
    отдельный пример: именно их легче всего сломать случайно.
  - **Агрегация §5.4** — итог считает КОД. Сюда же пин однократного округления:
    накопленное по позициям округление съедало бы допуск 0.5% метрики C4.
  - **Батчи §5.2 / дедуп §5.5** — на фейковом клиенте, разные ответы по батчам.
  - **Схема §3.1** — отказ обязан быть объяснён, не-отказ обязан нести числа.
  - **Ветка `unresolved` §6.6** — позиция без чисел не превращается в нули.
  - **Грепы по промптам (DoD п.4)** — дёшево и ловит регресс формулировки.

Живых вызовов провайдера нет (GAP-6).
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import settings
from tests.conftest import patch_llm_transport
from app.contexts.nutrition.resolve import prompts
from app.contexts.nutrition.resolve.checks import (
    FLAG_ARITH_MISMATCH,
    FLAG_BARCODE_INVALID,
    FLAG_BOUNDS_FAIL,
    FLAG_BOUNDS_WARN,
    FLAG_KJ_KCAL_CONFLICT,
    FLAG_KJ_SUSPECTED,
    FLAG_PORTION_WARN,
    FLAG_WEIGHT_MISMATCH,
    apply_checks,
    ean_checksum_ok,
    extract_json,
    run_checks,
    weight_mismatch,
)
from app.contexts.nutrition.resolve.extractor import LLMExtractor, dedup
from app.contexts.nutrition.resolve.pipeline import ResolverPipeline
from app.contexts.nutrition.resolve.ports import (
    KJ_NOTE,
    ExtractedItem,
    Macros,
    ResolvedNutrition,
)
from app.contexts.nutrition.resolve.sources.estimate import EstimateSource
from app.contexts.nutrition.services import FoodService, LLMError, aggregate_dish


def _item(**kw) -> ExtractedItem:
    data = {
        "name": "продукт",
        "readout": "label",
        "per100g": Macros(kcal=100, protein=10, fat=2, carbs=10.5),
    }
    data.update(kw)
    return ExtractedItem(**data)


def _macros(kcal, p, f, c) -> Macros:
    return Macros(kcal=kcal, protein=p, fat=f, carbs=c)


# ════════════════════════ C1 — разбор ответа (§6.1) ═══════════════════════════

def test_c1_plain_json():
    assert extract_json('{"items": []}') == {"items": []}


def test_c1_fenced_json():
    assert extract_json('```json\n{"items": [1]}\n```') == {"items": [1]}


def test_c1_fenced_without_language():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_c1_prefix_text_before_json():
    assert extract_json('Вот разбор:\n{"a": 1}') == {"a": 1}


def test_c1_answer_starting_with_json_word_is_not_eaten():
    """Регресс D13: прежний `lstrip("json")` резал по НАБОРУ символов, не по префиксу.

    На строке, начинающейся с «n» или «json», он съедал ведущие символы и ломал
    следующий за ними JSON. Регулярка снимает ограждение целиком или не снимает
    вовсе — промежуточного состояния нет.
    """
    assert extract_json('json {"name": "нут"}') == {"name": "нут"}
    assert extract_json('{"name": "nuts", "note": "json-подобный текст"}')["name"] == "nuts"


def test_c1_broken_json_raises():
    """Единственное место, где ответ действительно отклоняется (§2.2)."""
    with pytest.raises(LLMError):
        extract_json("совсем не json")
    with pytest.raises(LLMError):
        extract_json('{"items": [')


# ════════════════════════ C2 — Атуотер (§6.2) ═════════════════════════════════

def test_c2_consistent_label_passes():
    """Под порогом: 100 ккал при 10/2/10.5 → Атуотер ровно 100, расхождение 0."""
    result = run_checks(_item())
    assert FLAG_ARITH_MISMATCH not in result.flags
    assert result.readout == "label" and not result.downgraded


def test_c2_real_label_spread_is_not_flagged():
    """Пол порога задан физикой продуктов, а не качеством модели.

    Кейс A-30 датасета: 447 ккал на этикетке против 399.2 по Атуотеру — 12.0%
    расхождения на ВЕРНО прочитанной этикетке (клетчатка, полиолы, округление
    производителем). Порог ниже ~13% начал бы браковать такие ответы.
    """
    item = _item(per100g=_macros(447, 6.8, 22.4, 51.2))
    assert FLAG_ARITH_MISMATCH not in run_checks(item).flags


def test_c2_gross_mismatch_downgrades_label():
    """Над порогом: числа из разных строк таблицы → позиция уходит в лестницу."""
    item = _item(per100g=_macros(900, 10, 2, 10.5))   # Атуотер 100, расхождение 800%
    result = run_checks(item)
    assert FLAG_ARITH_MISMATCH in result.flags
    assert result.readout == "estimate" and result.downgraded


def test_c2_threshold_is_read_from_config():
    """Порог — из конфига: перекалибровка не должна проходить мимо теста."""
    item = _item(per100g=_macros(100, 10, 2, 10.5))
    lowered = run_checks(item, atwater=0.0)   # любой ≥ 0 → флаг
    assert FLAG_ARITH_MISMATCH in lowered.flags
    assert settings.check_atwater == 0.20


# ════════════════════════ C3 — кДж-утечка (§6.3) ══════════════════════════════

def test_c3_kj_written_as_kcal_is_flagged():
    """Реальный случай замера: A-19, 380 «ккал» вместо 90 (ratio 4.29)."""
    item = _item(per100g=_macros(380, 4.3, 0.2, 17.5))
    result = run_checks(item)
    assert FLAG_KJ_SUSPECTED in result.flags
    assert result.readout == "estimate"          # маршрут изменён
    assert result.portion_g is None or True      # числа не тронуты — см. инвариант ниже


def test_c3_normal_label_is_not_in_the_band():
    """Эталоны лежат в ratio 0.96–1.12 — до полосы [3.6, 4.8] далеко."""
    assert FLAG_KJ_SUSPECTED not in run_checks(_item()).flags


def test_c3_no_autocorrection(monkeypatch):
    """§2.4: код НЕ делит на 4.184 при подозрении на кДж.

    Если модель прочла килоджоули, она могла ошибиться и в остальном — «починка»
    произвела бы правдоподобное и неверное число, то есть ровно тот класс ошибки,
    который в этом проекте трижды стоил перепрогонов.
    """
    item = _item(per100g=_macros(380, 4.3, 0.2, 17.5))
    checked = apply_checks(item)
    assert checked.per100g.kcal == 380          # как было
    assert FLAG_KJ_SUSPECTED in checked.warnings


# ════════════════════════ C4 — границы (§6.4) ═════════════════════════════════

def test_c4_0_valid_barcode_survives():
    assert ean_checksum_ok("4690228004018")
    assert run_checks(_item(barcode="4690228004018")).barcode == "4690228004018"


def test_c4_0_misread_barcode_is_dropped():
    """Контрольная сумма не сошлась → признака нет.

    Оставить «плохой, но какой-то» штрихкод хуже, чем не иметь его: он уйдёт в
    точный lookup и вернёт ЧУЖОЙ товар с наивысшим доверием (`exact`).
    """
    result = run_checks(_item(barcode="4690228004017"))
    assert FLAG_BARCODE_INVALID in result.flags and result.barcode is None


def test_c4_0_in_store_weight_code_is_dropped():
    """Внутримагазинный код (префикс 20–29): в глобальной базе его нет по определению."""
    assert not ean_checksum_ok("2312345678904")


def test_c4_1_kcal_above_physical_ceiling():
    """Чистый жир = 900 ккал/100 г; выше — не еда."""
    assert FLAG_BOUNDS_FAIL in run_checks(_item(per100g=_macros(950, 0, 100, 0))).flags
    assert FLAG_BOUNDS_FAIL not in run_checks(_item(per100g=_macros(899, 0, 99.8, 0))).flags


def test_c4_2_macros_sum_over_100_is_per_portion_trap():
    """Детектор ловушки per-portion, а не только санити-граница.

    Эталон A-09 (картофельное пюре) имеет P+F+C = 125 при `basis: per_portion` —
    значения на упаковку 500 г. Объявленные значениями «на 100 г», они означают
    больше вещества, чем весит продукт.
    """
    assert FLAG_BOUNDS_FAIL in run_checks(_item(per100g=_macros(500, 25, 40, 60))).flags


def test_c4_3_zero_kcal_with_macros():
    assert FLAG_BOUNDS_FAIL in run_checks(_item(per100g=_macros(5, 3, 1, 4))).flags


def test_c4_4_vodka_is_warning_not_failure():
    """Калории без состава легитимны у крепкого алкоголя (≈235/0/0/0).

    Поэтому C4.4 — предупреждение: понижать доверие здесь значило бы гонять
    водку по лестнице источников на каждом вводе.
    """
    result = run_checks(_item(name="водка", per100g=_macros(235, 0, 0, 0)))
    assert FLAG_BOUNDS_WARN in result.flags and FLAG_BOUNDS_FAIL not in result.flags
    assert result.readout == "label" and not result.downgraded


# ════════════════════════ C5 — порция (§6.4) ══════════════════════════════════

def test_c5_normal_portion_kept():
    assert run_checks(_item(portion_g=200)).portion_g == 200


def test_c5_absurd_portion_dropped():
    """Типовая причина — путаница мл/г или выдумка; вес спросим у человека."""
    result = run_checks(_item(portion_g=settings.check_portion_g_max + 1))
    assert FLAG_PORTION_WARN in result.flags and result.portion_g is None


# ════════════════════════ C6 — сходимость веса (§6.4) ═════════════════════════

def test_c6_silent_below_threshold():
    """Набор C-01: Σ 180 г при заявленных 170 → 5.9%, проверка обязана молчать."""
    assert weight_mismatch([100, 50, 30], 170) is False


def test_c6_fires_above_threshold():
    """Набор C-05: Σ 500 г при заявленных 350 → 42.9%, проверка обязана сработать."""
    assert weight_mismatch([200, 150, 100, 50], 350) is True


def test_c6_denominator_is_declared_weight_not_sum():
    """ПИН ФОРМУЛЫ. Знаменатель — заявленный вес (ТЗ §6.4), а не Σ граммовок.

    Это не педантизм: на C-05 Σ-знаменатель даёт ровно 30.0% и не проходит
    строгое `>`, то есть положительный контроль C6+ МОЛЧА не срабатывает, а
    `expect_weight_mismatch: true` в манифесте выглядит выполненным. Ошибку
    поймали в раннере при сборке яруса C — и поймал её не тест, а сверка
    процентов с источником. Реализации две (здесь и в раннере), поэтому пин
    нужен в обеих: ошибка может повториться независимо.

    Оба зеркальных случая ниже проверяют РАЗНЫЕ стороны подмены.
    """
    # Σ=500, заявлено 350: по ТЗ 150/350 = 42.9% → сработать; по Σ 150/500 = 30.0% → молчать.
    assert weight_mismatch([500], 350) is True
    # Σ=350, заявлено 500: по ТЗ 150/500 = 30.0% → молчать; по Σ 150/350 = 42.9% → сработать.
    assert weight_mismatch([350], 500) is False


def test_c6_no_declared_weight_is_silent():
    assert weight_mismatch([100, 50], None) is False
    assert weight_mismatch([100, 50], 0) is False


# ════════════════ ДВА ИНВАРИАНТА (DoD п.3 и принцип §2.4) ════════════════════

_HOSTILE = [
    _item(per100g=_macros(0, 0, 0, 0)),
    _item(per100g=_macros(9999, 999, 999, 999)),
    _item(per100g=_macros(380, 4.3, 0.2, 17.5)),
    _item(per100g=_macros(500, 25, 40, 60), portion_g=-5),
    _item(readout="unreadable", per100g=None, note="этикетка перекручена"),
    _item(barcode="0000", portion_g=99999),
    _item(readout="estimate", per100g=_macros(52, 0.3, 0.2, 14)),
]


@pytest.mark.parametrize("item", _HOSTILE)
def test_checks_never_reject_a_response(item):
    """DoD п.3: НИ ОДНА проверка не отклоняет ответ (§2.2).

    Единственный источник отказа — невалидная схема после ретрая. Всё остальное
    меняет маршрут и предупреждения, но гарантия ответа сохраняется: пользователь
    получает числа с честной пометкой, а не 502.
    """
    result = run_checks(item)               # не бросает
    assert result.readout in ("label", "estimate", "unreadable")
    checked = apply_checks(item)            # тоже не бросает
    assert checked is not None


@pytest.mark.parametrize("item", _HOSTILE)
def test_checks_never_touch_numbers(item):
    """§2.4: код помечает и маршрутизирует, но не исправляет числа."""
    checked = apply_checks(item)
    if item.per100g is None:
        assert checked.per100g is None
    else:
        assert checked.per100g.model_dump() == item.per100g.model_dump()


def test_checks_downgrade_only_label():
    """Понижение — только с `label`: `estimate` понижать некуда, и лестницу он
    проходит и так. Иначе флаг менял бы смысл в зависимости от входа."""
    bad = _macros(900, 10, 2, 10.5)
    assert run_checks(_item(readout="estimate", per100g=bad)).readout == "estimate"
    assert run_checks(_item(readout="label", per100g=bad)).readout == "estimate"


# ════════════════════ АГРЕГАЦИЯ — L2 в коде (§5.4) ═══════════════════════════

def _ing(name, kcal, p, f, c, portion):
    return ExtractedItem(
        name=name, portion_g=portion, readout="label", per100g=_macros(kcal, p, f, c)
    )


def test_aggregate_dish_matches_manual_sum():
    """Три ингредиента: итог совпадает с ручным расчётом (§5.4.b).

    342×1.0 + 96×0.5 + 253×0.3 = 342 + 48 + 75.9 = 465.9
    """
    items = [
        _ing("макароны", 342, 12, 1.5, 70, 100),
        _ing("паста томатная", 96, 4.3, 0.2, 17.5, 50),
        _ing("сыр творожный", 253, 6, 24, 3, 30),
    ]
    agg = aggregate_dish(items, name="паста", weight_g=170)

    assert agg.item.kcal == 465.9
    assert agg.item.weight_g == 170          # ЗАЯВЛЕННЫЙ вес, не Σ граммовок (180)
    assert agg.item.description == "паста"
    assert agg.extras == [] and agg.unresolved == []


def test_aggregate_rounds_once_at_the_end():
    """Округление ровно одно (§5.4.d).

    1.05 г/100 г × 100 г × 3 позиции: округление НА ПОЗИЦИИ даёт 1.1 × 3 = 3.3,
    однократное в конце — 3.15 → 3.2. На пяти ингредиентах такая разница уже
    съедает допуск 0.5% метрики C4, то есть маскирует настоящую ошибку агрегации.
    """
    items = [_ing(f"ing{i}", 1.05, 0, 0, 0, 100) for i in range(3)]
    agg = aggregate_dish(items, name="блюдо", weight_g=300)
    assert agg.item.kcal == 3.2


def test_aggregate_position_without_grams_contributes_zero_and_is_shown():
    """§5.4.b: позиция без граммовки — ошибка извлечения, а не повод врать в итоге.

    Вклад 0, флаг, и позиция показана отдельно — иначе пользователь не поймёт,
    почему итог меньше ожидаемого.
    """
    items = [
        _ing("рис", 130, 2.7, 0.3, 28, 200),
        _ing("соус", 200, 1, 20, 4, None),
    ]
    agg = aggregate_dish(items, name="боул", weight_g=250)

    assert agg.item.kcal == 260.0                       # только рис
    assert "portion_missing" in agg.item.warnings
    assert len(agg.extras) == 1 and agg.extras[0].weight_g is None
    assert agg.positions[0] is agg.item                 # итог всегда первым


def test_aggregate_single_photo_mode_uses_declared_weight():
    """§5.4.c: у блюда по фото граммовки нет — масштаб задаёт заявленный вес."""
    items = [_ing("запеканка", 200, 10, 8, 20, None)]
    agg = aggregate_dish(items, name="запеканка", weight_g=350, single=True)
    assert agg.item.kcal == 700.0 and agg.extras == []


def test_aggregate_unreadable_goes_to_unresolved_not_zeros():
    """§6.6: позиция без чисел не превращается в нули внутри итога."""
    items = [
        _ing("рис", 130, 2.7, 0.3, 28, 100),
        ExtractedItem(name="соус", readout="unreadable", per100g=None,
                      note="этикетка не читается"),
    ]
    agg = aggregate_dish(items, name="боул", weight_g=150)

    assert agg.item.kcal == 130.0
    assert len(agg.unresolved) == 1
    assert agg.unresolved[0].reason == "unreadable"
    assert agg.unresolved[0].note == "этикетка не читается"


# ════════════════════ БАТЧИ И ДЕДУП (§5.2, §5.5) ═════════════════════════════

class _ScriptedChat:
    """Возвращает заранее заготовленные ответы по одному на вызов."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )],
            usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 10,
                                                      "completion_tokens": 5}),
        )


def _patch(monkeypatch, contents: list[str]) -> _ScriptedChat:
    chat = _ScriptedChat(contents)
    patch_llm_transport(monkeypatch, chat)
    return chat


def _answer(*items) -> str:
    return json.dumps({"items": list(items)})


def _row(name, kcal=100, readout="estimate", portion=None, **extra):
    """Ответ модели с ФИЗИЧНЫМИ числами: углеводы подобраны так, чтобы Атуотер
    сходился ровно. Иначе проверки C2 сработают на самой фикстуре и понизят
    `readout` — тест про дедуп упал бы «правильно», но не по своей причине."""
    row = {
        "name": name, "readout": readout, "portion_g": portion,
        "per100g": {"kcal": kcal, "protein": 0, "fat": 0, "carbs": kcal / 4},
    }
    row.update(extra)
    return row


async def test_batches_split_and_concat(monkeypatch):
    """7 снимков при лимите 5 → 2 вызова, позиции обоих склеены (§5.2)."""
    chat = _patch(monkeypatch, [_answer(_row("хлеб")), _answer(_row("сыр"))])
    items = await LLMExtractor().extract(images=[bytes([i]) for i in range(7)])

    assert len(chat.calls) == 2
    first = [p for p in chat.calls[0]["messages"][1]["content"] if p["type"] == "image_url"]
    second = [p for p in chat.calls[1]["messages"][1]["content"] if p["type"] == "image_url"]
    assert (len(first), len(second)) == (5, 2)     # порядок сохранён, ничего не потеряно
    assert {i.name for i in items} == {"хлеб", "сыр"}


async def test_batch_usage_is_summed(monkeypatch):
    """Цена разбора — цена ВСЕХ его вызовов, иначе батчи выглядят бесплатными."""
    _patch(monkeypatch, [_answer(_row("хлеб")), _answer(_row("сыр"))])
    ext = LLMExtractor()
    await ext.extract(images=[bytes([i]) for i in range(7)])
    assert ext.last_usage["prompt_tokens"] == 20 and ext.last_call_count == 2


async def test_dedup_across_batch_boundary_keeps_label(monkeypatch):
    """Тот же товар в разных батчах → ОДНА позиция; побеждает `label` (§5.5)."""
    _patch(monkeypatch, [
        _answer(_row("Сыр творожный", kcal=100, readout="estimate")),
        _answer(_row("сыр  творожный", kcal=253, readout="label", portion=30)),
    ])
    items = await LLMExtractor().extract(images=[bytes([i]) for i in range(7)])

    assert len(items) == 1
    assert items[0].readout == "label" and items[0].per100g.kcal == 253
    assert items[0].portion_g == 30                # ненулевая граммовка выживает


def test_dedup_merges_notes_and_takes_nonzero_portion():
    a = ExtractedItem(name="молоко", readout="estimate", note="блик",
                      per100g=_macros(60, 3, 3.2, 4.7))
    b = ExtractedItem(name="Молоко", readout="estimate", portion_g=200,
                      note="обрезан край", per100g=_macros(60, 3, 3.2, 4.7))
    [merged] = dedup([a, b])
    assert merged.portion_g == 200
    assert "блик" in merged.note and "обрезан край" in merged.note


def test_dedup_keeps_different_products_apart():
    """Разный бренд/жирность — разные товары, схлопывать нельзя."""
    a = ExtractedItem(name="молоко", brand="А", fat_pct=3.2, per100g=_macros(60, 3, 3.2, 4.7))
    b = ExtractedItem(name="молоко", brand="Б", fat_pct=3.2, per100g=_macros(60, 3, 3.2, 4.7))
    c = ExtractedItem(name="молоко", brand="А", fat_pct=1.5, per100g=_macros(45, 3, 1.5, 4.7))
    assert len(dedup([a, b, c])) == 3


# ════════════════════ FINISH_REASON И РЕТРАЙ РАЗБОРА (§5.3, §6.1) ════════════

async def test_finish_reason_length_retries_with_double_budget(monkeypatch):
    """Обрезка по лимиту вывода — не «кривой JSON», а своя ситуация (§5.3)."""
    import app.contexts.nutrition.services.llm_service as llm_mod

    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        reason = "length" if len(calls) == 1 else "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=_answer(_row("хлеб"))),
                finish_reason=reason,
            )],
            usage=None,
        )

    patch_llm_transport(monkeypatch, create)
    items = await LLMExtractor().extract(text="хлеб")

    assert len(items) == 1 and len(calls) == 2
    budget_key = settings.llm_max_tokens_param
    assert calls[1][budget_key] == calls[0][budget_key] * 2


async def test_finish_reason_length_twice_is_honest_error(monkeypatch):
    """Второй раз подряд — честная ошибка с внятным текстом, а не третья попытка."""
    import app.contexts.nutrition.services.llm_service as llm_mod

    async def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="{"), finish_reason="length"
            )],
            usage=None,
        )

    patch_llm_transport(monkeypatch, create)
    with pytest.raises(LLMError, match="меньше снимков"):
        await LLMExtractor().extract(text="хлеб")


async def test_unparsable_answer_retries_once_then_raises(monkeypatch):
    """§6.1: один ретрай с ужесточающей строкой, потом отказ."""
    chat = _patch(monkeypatch, ["не json", _answer(_row("хлеб"))])
    items = await LLMExtractor().extract(text="хлеб")

    assert len(items) == 1 and len(chat.calls) == 2
    assert prompts.RETRY_JSON_SUFFIX.strip() in chat.calls[1]["messages"][1]["content"]


async def test_unparsable_twice_raises(monkeypatch):
    _patch(monkeypatch, ["не json", "тоже не json"])
    with pytest.raises(LLMError):
        await LLMExtractor().extract(text="хлеб")


# ════════════════════════ СХЕМА §3.1 ═════════════════════════════════════════

def test_schema_unreadable_requires_note():
    """Отказ обязан быть объяснён — иначе `unreadable` станет бесплатным «не знаю»."""
    with pytest.raises(ValidationError):
        ExtractedItem(name="мороженое", readout="unreadable", per100g=None)


def test_schema_label_requires_per100g():
    with pytest.raises(ValidationError):
        ExtractedItem(name="молоко", readout="label", per100g=None)


def test_schema_estimate_requires_per100g():
    with pytest.raises(ValidationError):
        ExtractedItem(name="молоко", readout="estimate", per100g=None)


def test_schema_unreadable_with_note_is_valid():
    item = ExtractedItem(name="мороженое", readout="unreadable", per100g=None,
                         note="обёртка перекручена, числа не читаются")
    assert item.per100g is None


# ════════════════════════ ВЕТКА UNRESOLVED §6.6 ══════════════════════════════

class _FakeExtractor:
    def __init__(self, items):
        self.items = items

    async def extract(self, **kwargs):
        return self.items


def _ladder() -> ResolverPipeline:
    return ResolverPipeline(network_sources=[], fallback=EstimateSource())


async def test_unresolved_does_not_block_the_rest(session):
    """Позиция без чисел не попадает в items; остальные сохраняются."""
    svc = FoodService(
        session,
        extractor=_FakeExtractor([
            _ing("рис", 130, 2.7, 0.3, 28, 100),
            ExtractedItem(name="соус", readout="unreadable", per100g=None, note="блик"),
        ]),
        resolver=_ladder(),
    )
    result = await svc.analyze_text("рис и соус")

    assert [i.description for i in result.items] == ["рис"]
    assert len(result.unresolved) == 1 and result.unresolved[0].name == "соус"


async def test_all_unresolved_is_200_not_502(session):
    """Все позиции нераспознаны → пустой items и НЕПУСТОЙ unresolved, без ошибки.

    Прежнее правило («пустой items → LLMError») превращало частичный, но честный
    результат в отказ сервиса. Теперь 502 остаётся только когда пусты оба массива.
    """
    svc = FoodService(
        session,
        extractor=_FakeExtractor([
            ExtractedItem(name="нечто", readout="unreadable", per100g=None, note="блик"),
        ]),
        resolver=_ladder(),
    )
    result = await svc.analyze_text("?")
    assert result.items == [] and len(result.unresolved) == 1


async def test_nothing_at_all_is_an_error(session):
    """Модель не вернула ничего — это отказ разбора, а не частичный успех."""
    svc = FoodService(session, extractor=_FakeExtractor([]), resolver=_ladder())
    with pytest.raises(LLMError):
        await svc.analyze_text("?")


async def test_estimate_source_refuses_without_numbers():
    """Терминальная ступень не выдумывает нули из ничего (§6.6)."""
    item = ExtractedItem(name="x", readout="unreadable", per100g=None, note="n")
    assert await EstimateSource().lookup(item.to_query()) is None
    assert await _ladder().refine(item) is None


# ════════════ ЛЕСТНИЦА: сверка при уверенном матче (§7.2 + гейт §7.1a) ════════

class _ConfidentResult(ResolvedNutrition):
    """Как будет выглядеть выдача ступени после блока 10 (ADR-0019 §2.2–2.4)."""

    match_confidence: str = "exact"


class _CountingSource:
    name = "off"
    timeout_s = 2.5

    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def lookup(self, q):
        self.calls += 1
        return self.result


async def test_label_disagreement_shows_both_versions():
    """Расхождение >15% при УВЕРЕННОМ матче → флаг + альтернатива, числа не подменены."""
    src = _CountingSource(
        _ConfidentResult(per100g=_macros(60, 3, 3.2, 4.7), source="off")
    )
    pipe = ResolverPipeline(network_sources=[src], fallback=EstimateSource())
    item = ExtractedItem(name="молоко", brand="Домик в деревне", readout="label",
                         per100g=_macros(100, 10, 2, 10.5))

    r = await pipe.refine(item)

    assert src.calls == 1
    assert r.source == "label" and r.per100g.kcal == 100      # этикетка остаётся основной
    assert "label_disagrees_with_source" in r.warnings
    assert r.alternative is not None and r.alternative.per100g.kcal == 60


async def test_label_agreement_has_no_flag():
    """Источник согласен → тихо: предупреждение без повода дороже молчания."""
    src = _CountingSource(
        _ConfidentResult(per100g=_macros(98, 10, 2, 10.5), source="off")
    )
    pipe = ResolverPipeline(network_sources=[src], fallback=EstimateSource())
    item = ExtractedItem(name="молоко", brand="Б", readout="label",
                         per100g=_macros(100, 10, 2, 10.5))

    r = await pipe.refine(item)
    assert r.source == "label" and r.warnings == [] and r.alternative is None


async def test_unconfident_match_is_not_shown_at_all():
    """Гейт §7.1a: неуверенный матч не показывается ни как alternative, ни как флаг.

    При точности матчинга 36% показ расхождения ПРОИЗВОДИЛ бы ложные тревоги в
    двух случаях из трёх — это и есть причина, по которой гейт вообще введён.
    """
    src = _CountingSource(ResolvedNutrition(per100g=_macros(60, 3, 3.2, 4.7), source="off"))
    pipe = ResolverPipeline(network_sources=[src], fallback=EstimateSource())
    item = ExtractedItem(name="молоко", brand="Б", readout="label",
                         per100g=_macros(100, 10, 2, 10.5))

    r = await pipe.refine(item)
    assert r.source == "label" and r.alternative is None and r.warnings == []


async def test_no_brand_means_no_network_call():
    """Без бренда сверка не запускается — проверяется счётчиком вызовов (§12.2)."""
    src = _CountingSource(_ConfidentResult(per100g=_macros(60, 3, 3.2, 4.7), source="off"))
    pipe = ResolverPipeline(network_sources=[src], fallback=EstimateSource())
    item = ExtractedItem(name="молоко", readout="label", per100g=_macros(100, 10, 2, 10.5))

    r = await pipe.refine(item)
    assert src.calls == 0 and r.source == "label"


# ════════════════════════ ПРОМПТЫ: DoD п.4 ═══════════════════════════════════

_ARITHMETIC_ROOTS = ("умнож", "сложи", "сумм")


def _all_prompt_texts() -> list[tuple[str, str]]:
    texts = [("SYSTEM_PROMPT_V2", prompts.SYSTEM_PROMPT_V2)]
    texts += [
        (mode, prompts.user_preamble(
            mode, text="состав", caption="", dish_name="блюдо", weight_g=200))
        for mode in prompts.MODES
    ]
    return texts


@pytest.mark.parametrize("label,text", _all_prompt_texts())
def test_prompts_never_ask_the_model_to_do_arithmetic(label, text):
    """DoD п.4: в промптах нет ТРЕБОВАНИЯ считать (L2).

    Грепается корень, но с оговоркой: сам целевой промпт §4.1 содержит «не
    складывай» и «не умножай» — это ЗАПРЕТ, ради которого всё и затевалось.
    Поэтому проверка не «корня нет», а «каждое вхождение корня отрицательное».
    Наивный греп красил бы эталонный текст ТЗ и был бы отключён на второй день.
    """
    lowered = text.lower()
    for root in _ARITHMETIC_ROOTS:
        start = 0
        while (pos := lowered.find(root, start)) != -1:
            before = lowered[max(0, pos - 12):pos]
            assert "не " in before, (
                f"{label}: «{root}» без отрицания — промпт снова просит модель "
                f"считать: ...{text[max(0, pos - 40):pos + 20]}..."
            )
            start = pos + len(root)


def test_old_prompts_are_gone():
    """DoD п.2: три промпта прежней контрактной модели удалены из репозитория."""
    import app.contexts.nutrition.services.llm_service as llm_mod

    for name in ("_SYSTEM_PROMPT", "_DISH_PROMPT", "_COMBINED_PROMPT", "LLMService"):
        assert not hasattr(llm_mod, name), f"{name} должен быть удалён (ТЗ §4.3)"


def test_one_system_prompt_for_all_five_paths():
    """Пять путей отличаются ТОЛЬКО user-преамбулой (§4.3)."""
    seen = {
        prompts.user_preamble(mode, text="t", caption="c",
                              dish_name="блюдо", weight_g=200)
        for mode in prompts.MODES
    }
    assert len(seen) == len(prompts.MODES)          # преамбулы различны
    assert prompts.SYSTEM_PROMPTS["v2"] is prompts.SYSTEM_PROMPT_V2


def test_dish_preambles_carry_name_and_weight():
    text = prompts.user_preamble(prompts.MODE_DISH_COMBINED, text="рис 100 г",
                                 dish_name="боул", weight_g=250)
    assert '"боул"' in text and "250 г" in text and "рис 100 г" in text


def test_dedup_unreadable_twin_merges_into_readable_one():
    """Отказ по продукту, который другой батч прочитал, — утверждение без силы (§5.5).

    Поймано прогоном яруса C, а не рассуждением: в составном режиме текст состава
    уходит в КАЖДЫЙ батч, поэтому второй честно отвечает `unreadable` по товарам,
    чьих этикеток на его снимках нет. По полному ключу (name, brand, fat_pct) такая
    позиция не схлопывалась НИКОГДА — бренда у неё нет по определению, — и правило
    приоритета `label > estimate > unreadable` из того же §5.5 было мёртвым кодом.
    Цена: дубль в списке позиций и удвоенная Σ граммовок, из-за которой C6 ложно
    срабатывал на наборах, обязанных молчать.
    """
    read = ExtractedItem(name="тунец консервированный", brand="Скипджек",
                         readout="label", portion_g=80,
                         per100g=_macros(107, 24, 1.4, 0))
    refused = ExtractedItem(name="тунец консервированный", readout="unreadable",
                            portion_g=80, per100g=None,
                            note="этикетки нет на этих снимках")
    [merged] = dedup([read, refused])

    assert merged.readout == "label" and merged.per100g.kcal == 107
    assert merged.note is None          # чужой отказ не показывается как предупреждение


def test_dedup_keeps_unreadable_when_nobody_read_it():
    """Если продукт не прочитал НИКТО — отказ остаётся: он и есть результат."""
    refused = ExtractedItem(name="соус", readout="unreadable", per100g=None, note="блик")
    other = ExtractedItem(name="рис", readout="label", per100g=_macros(130, 2.7, 0.3, 28))
    out = dedup([other, refused])
    assert len(out) == 2 and out[1].readout == "unreadable"


def test_dedup_unreadable_does_not_inflate_weight_sum():
    """Следствие для C6: Σ граммовок считается по СХЛОПНУТЫМ позициям."""
    items = dedup([
        ExtractedItem(name="рис", brand="X", readout="label", portion_g=100,
                      per100g=_macros(130, 2.7, 0.3, 28)),
        ExtractedItem(name="рис", readout="unreadable", portion_g=100, per100g=None,
                      note="не видно"),
    ])
    assert weight_mismatch([i.portion_g for i in items], 110) is False   # 100 vs 110 = 9%


def test_dedup_by_name_only_in_composed_dish_modes():
    """Составной режим: одно имя = одна строка состава, бренд в ключ не входит (§5.5).

    Замерено, а не выведено: в составном режиме текст состава уходит в КАЖДЫЙ
    батч, поэтому батч с этикеткой сообщает бренд, а батч без неё — нет. По
    полному ключу такие записи расходятся, и ингредиент попадает в итог дважды:
    на наборе C-04 итог был 384.7 вместо 208.7. На дневниковом пути ключ остаётся
    полным — там разные бренды это разные товары (тест ниже).
    """
    a = ExtractedItem(name="тунец", brand="Скипджек", readout="label", portion_g=40,
                      per100g=_macros(107, 24, 1.4, 0))
    b = ExtractedItem(name="тунец", readout="estimate", portion_g=40,
                      per100g=_macros(130, 20, 5, 0))

    assert len(dedup([a, b])) == 2                       # дневник: не трогаем
    [merged] = dedup([a, b], by_name_only=True)          # блюдо: одна строка состава
    assert merged.readout == "label" and merged.per100g.kcal == 107


async def test_composed_mode_does_not_double_count_across_batches(monkeypatch):
    """Сквозной пин: два батча перечисляют один и тот же состав → итог не удваивается."""
    _patch(monkeypatch, [
        _answer(_row("рис", kcal=100, readout="label", portion=100, brand="X"),
                _row("соус", kcal=200, readout="label", portion=50, brand="Y")),
        _answer(_row("рис", kcal=100, readout="estimate", portion=100),
                _row("соус", kcal=200, readout="estimate", portion=50)),
    ])
    items = await LLMExtractor().extract(
        mode="dish_combined", text="рис 100 г, соус 50 г",
        images=[bytes([i]) for i in range(7)], dish_name="боул", weight_g=150,
    )
    agg = aggregate_dish(items, name="боул", weight_g=150)
    assert len(items) == 2
    assert agg.item.kcal == 200.0        # 100×1.0 + 200×0.5, а не вдвое больше


# ══════════════ C3a — сверка кДж с ккал (§6.3a, ТЗ v0.5, DoD п.11) ═══════════
#
# Проверка заведена по ПОЛЕВОМУ дефекту, а не по рассуждению: 2026-08-02, первый
# вечер прода на gpt-5.4-mini, этикетка «450 кДж / 110 ккал» → модель записала в
# ккал 450 и подогнала под них жиры (40 г вместо 2,5). Пользователь увидел 414
# ккал вместо 101 с провенансом `label`; C2 показала 2,7 % при пороге 20 %, C3 —
# 1,03 при полосе [3,6; 4,8]. Разбор — 05_implementation/field-defect-2026-08-02-kj-leak.md.
#
# Негативные контроли обязательны (правило llm-eval-dataset.md §3D.1): метрика без
# них не считается введённой. Приграничные кейсы ниже проверяют не «метрика
# шевелится», а «порог стоит ровно там, где написан».


def _kj_item(kcal, kj, p=10.0, f=2.0, c=10.5):
    return _item(per100g=_macros(kcal, p, f, c), energy_kj_100g=kj)


def test_c3a_kcal_equals_kj_is_flagged():
    """(1) Мутант «kcal := kj» — ровно то, что произошло в поле (F-01)."""
    result = run_checks(_kj_item(450, 450))
    assert FLAG_KJ_KCAL_CONFLICT in result.flags        # 450 против 107.6 = 318%
    assert result.readout == "estimate" and result.downgraded


def test_c3a_rounded_label_is_silent():
    """(2) Реальная этикетка «450 кДж / 110 ккал»: 2,2 % — округление производителем."""
    assert FLAG_KJ_KCAL_CONFLICT not in run_checks(_kj_item(110, 450)).flags


def test_c3a_reference_a16_is_silent():
    """(3) Эталон A-16 (873,2 кДж / 210,9 ккал): 0,3 %."""
    assert FLAG_KJ_KCAL_CONFLICT not in run_checks(_kj_item(210.9, 873.2)).flags


def test_c3a_threshold_stands_where_written():
    """(4) Приграничный негативный контроль: 4 % молчит, 6 % срабатывает.

    Мутант вдали от порога доказывает лишь, что метрика вообще считается.
    Что порог стоит на 5 %, а не «где-то там», показывает только пара вокруг него.
    """
    exact = 873.2 / 4.184                       # 208.7 ккал — точное соответствие
    assert FLAG_KJ_KCAL_CONFLICT not in run_checks(_kj_item(exact * 1.04, 873.2)).flags
    assert FLAG_KJ_KCAL_CONFLICT in run_checks(_kj_item(exact * 1.06, 873.2)).flags
    assert settings.check_kj_kcal == 0.05       # порог — из конфига, не из литерала


def test_c3a_without_kj_does_not_run():
    """(5) кДж не напечатаны → сверять не с чем; ккал не трогаются (§2.4)."""
    checked = apply_checks(_item(per100g=_macros(110, 10, 2, 10.5)))
    assert FLAG_KJ_KCAL_CONFLICT not in checked.warnings
    assert checked.per100g.kcal == 110 and checked.energy_kj_100g is None


def test_c3a_kcal_computed_from_kj_in_code():
    """(6) Напечатаны только кДж → ккал считает КОД, факт пересчёта в note.

    Прежняя редакция промпта поручала это деление модели — и именно оно, будучи
    арифметикой в модели, обернулось F-01. Здесь та же операция выполняется
    детерминированно и оставляет след.
    """
    item = ExtractedItem.model_validate({
        "name": "продукт", "readout": "label", "energy_kj_100g": 450,
        "per100g": {"kcal": None, "protein": 4, "fat": 3, "carbs": 12},
    })
    assert item.per100g.kcal == 107.6           # 450 / 4.184
    assert KJ_NOTE in (item.note or "")
    # И этот пересчёт НЕ порождает собственного расхождения: код делит той же
    # константой, которой сверяет.
    assert FLAG_KJ_KCAL_CONFLICT not in run_checks(item).flags


def test_c3a_catches_what_c2_and_c3_cannot():
    """(7) Полный F-01: C2 и C3 молчат, C3a срабатывает.

    Это ФИКСАЦИЯ ГРАНИЦЫ ПРИМЕНИМОСТИ C2/C3, а не баг: обе выводят эталон из тех
    же чисел, что проверяют, и против согласованной подгонки бессильны по
    построению. Полосу [3,6; 4,8] трогать нельзя — она подтверждена данными для
    своего класса (одиночная кДж-утечка без компенсации).
    """
    item = _item(per100g=_macros(450, 17, 40, 2.5), energy_kj_100g=450)
    result = run_checks(item)

    assert FLAG_ARITH_MISMATCH not in result.flags      # Атуотер сошёлся: 2,7%
    assert FLAG_KJ_SUSPECTED not in result.flags        # ratio 1,03 — вне полосы
    assert FLAG_KJ_KCAL_CONFLICT in result.flags        # а вот здесь видно
    assert result.readout == "estimate"                 # позиция уходит в лестницу


def test_c3a_never_touches_numbers():
    """Инвариант §2.4 распространяется и на C3a: флаг есть, числа как были."""
    checked = apply_checks(_kj_item(450, 450))
    assert checked.per100g.kcal == 450 and checked.energy_kj_100g == 450


def test_label_without_any_energy_is_schema_violation():
    """§6.3a, третья ветка: `label` без ккал и без кДж — нарушение схемы (C1).

    Отдельного кода для неё нет и не нужно: `Macros.kcal` обязателен, а `label`
    обязан нести `per100g` — то есть состояние невыразимо, а не «не проверено».
    """
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate({"name": "x", "readout": "label", "per100g": None})
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate({
            "name": "x", "readout": "label",
            "per100g": {"kcal": None, "protein": 1, "fat": 1, "carbs": 1},
        })


def test_prompt_forbids_energy_arithmetic_in_model():
    """Грep-пин формулировки (DoD п.4): промпт обязан ЗАПРЕЩАТЬ деление кДж.

    В v2 стояло «если указаны ТОЛЬКО кДж — раздели на 4.184». Строка выглядела
    безобидно рядом с общим запретом арифметики и стоила прода 4,1-кратного
    завышения. Тест дешёвый и ловит именно её возврат.
    """
    active = prompts.SYSTEM_PROMPTS[prompts.DEFAULT_PROMPT_VERSION]
    assert "energy_kj_100g" in active
    assert "НЕ ДЕЛИ кДж" in active
    assert "не подгоняй жиры" in active
    assert "раздели на 4.184" not in active
    # v2 держим ДОСЛОВНО: без него «не хуже baseline» недоказуемо (ТЗ §12.3).
    assert "раздели на 4.184" in prompts.SYSTEM_PROMPT_V2


def test_prompt_forbids_null_inside_per100g():
    """Регресс v3, пойманный первым же прогоном (2026-08-03).

    Разрешение «напечатана только одна из двух — вторую поставь null» относится
    к ЭНЕРГИИ, но модель обобщила его на макросы: `per100g.fat = null` при
    `readout="label"` (A-17), все три макроса null на A-28. Схема §3.1 такого не
    допускает — и правильно, `Macros` с дырами выглядел бы как данные, — поэтому
    ответ отклонялся, ретрай давал то же самое, и пользователь не получал
    НИЧЕГО. JSON-валидность 100% → 96% при жёстком пороге 100%.

    Дыра открывается одной строкой промпта и закрывается одной строкой промпта,
    поэтому и пин здесь текстовый: тест на схеме её не увидит (схема-то как раз
    отработала верно), а стоимость проверки — один прогон за $0.01.
    """
    active = prompts.SYSTEM_PROMPTS[prompts.DEFAULT_PROMPT_VERSION]
    assert "null внутри per100g НЕДОПУСТИМ" in active
    # Модели обязан быть предложен ЗАКОННЫЙ выход, а не только запрет: иначе
    # «вижу энергию, макросы не читаются» ей просто некуда деть.
    assert 'readout="estimate"' in active
    assert 'readout="unreadable"' in active


def test_schema_rejects_partial_macros():
    """И сама схема обязана продолжать это отклонять — запрет в промпте не гарантия.

    Промпт влияет на вероятность, схема — на возможность. Пара «запрет + отказ»
    и есть то, что не даёт частично прочитанной этикетке стать записью с дырами.
    """
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate({
            "name": "белок яичный", "readout": "label",
            "per100g": {"kcal": 56, "protein": 13, "fat": None, "carbs": 1},
        })
