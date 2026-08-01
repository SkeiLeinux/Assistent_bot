"""resolve/checks.py — проверки ответа модели (ТЗ контракта §6).

Чистые функции: ни сети, ни LLM, ни настроек по умолчанию, которые нельзя
переопределить аргументом. Всё, что нужно для теста — на входе.

**Два инварианта, каждый под тестом (`tests/test_nutrition_checks.py`):**

1. **Ни одна проверка не отклоняет ответ** (§2.2). Единственный источник отказа —
   невалидная схема после ретрая. Проверки меняют МАРШРУТ (понижают `readout`,
   отправляя позицию в лестницу) и добавляют флаги для экрана подтверждения.
2. **Автокоррекция чисел запрещена** (§2.4). Код не делит на 4.184 при подозрении
   на кДж и не «чинит» вышедшие за границы значения. Он помечает и маршрутизирует;
   исправляет источник или человек. Обнуляются только ИДЕНТИФИКАТОРЫ, доверять
   которым нельзя (штрихкод с несошедшейся контрольной суммой, абсурдная порция) —
   это не коррекция числа, а отказ от заведомо ложного признака.

**Чем эти проверки на самом деле являются.** Калибровка (`llm-checks-calibration-
2026-07-27.md` §3) показала: на выбранной прод-модели `gpt-5.4-mini` C2–C4 ловят
**0 из 2** её ошибок, на `qwen/qwen3.6-27b` — 6 из 9. То есть это не защита от
остаточной ошибки текущей модели, а **детектор её подмены** (провайдер менял
модель под проектом трижды за 5 месяцев) и страховка вторичного таргета ADR-0018.
Единственный механизм против остаточных 63% — сверка с внешним источником (§7).
Формулировка «проверки закрывают остаточную ошибку» была бы неверной.

Пороги — `app/config.py` (`check_*`), обоснование каждого — отчёт калибровки.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.config import settings
from app.contexts.nutrition.resolve.ports import ExtractedItem, Macros, Readout
from app.contexts.nutrition.services.llm_service import LLMError

# ── Коды флагов (транспорт до фронта; человекочитаемый текст — на клиенте) ────

FLAG_ARITH_MISMATCH = "arith_mismatch"      # C2: Атуотер не сходится
FLAG_KJ_SUSPECTED = "kj_suspected"          # C3: похоже, в ккал записаны кДж
FLAG_BOUNDS_FAIL = "bounds_fail"            # C4.1–C4.3: физически невозможные числа
FLAG_BOUNDS_WARN = "bounds_warn"            # C4.4: подозрительно, но бывает легитимно
FLAG_BARCODE_INVALID = "barcode_invalid"    # C4.0: контрольная сумма EAN не сошлась
FLAG_PORTION_WARN = "portion_warn"          # C5: порция вне (0, 5000]
FLAG_WEIGHT_MISMATCH = "weight_mismatch"    # C6: Σ граммовок ≠ заявленный вес

# Флаги, понижающие доверие к «этикетке»: числа могли быть прочитаны не из той
# строки/колонки, поэтому позиция обязана пройти лестницу, а не коротить её.
_DOWNGRADING = frozenset({FLAG_ARITH_MISMATCH, FLAG_KJ_SUSPECTED, FLAG_BOUNDS_FAIL})

# Внутримагазинные (весовые) префиксы EAN-13: такие коды печатает касса магазина,
# в глобальных базах товаров их нет по определению.
_IN_STORE_PREFIXES = ("02",) + tuple(str(n) for n in range(20, 30))

# ```-ограждение ответа. Ровно префикс+суффикс, а не набор символов: прежний
# `lstrip("json")` срезал ЛЮБЫЕ ведущие j/s/o/n — на ответе, начинающемся с
# «json» он работал, а на «{"items"...}» после ```-строки съедал бы лишнее.
_FENCE = re.compile(r"^```[ \t]*(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```$", re.S)


@dataclass
class CheckResult:
    """Итог проверок одной позиции. Числа НЕ меняются — меняются маршрут и флаги."""

    flags: list[str] = field(default_factory=list)
    readout: Readout = "estimate"
    portion_g: float | None = None
    barcode: str | None = None
    downgraded: bool = False


# ── C1 — разбор ответа (ТЗ §6.1) ─────────────────────────────────────────────

def extract_json(content: str) -> dict:
    """Достаёт JSON-объект из ответа модели.

    Порядок: снять ```-ограждение регуляркой по ПРЕФИКСУ (D13), затем взять
    внешние скобки, затем `json.loads`. Ошибка разбора — единственное место, где
    ответ действительно отклоняется; вызывающий делает один ретрай с ужесточающей
    строкой (§6.1), и только потом это доходит до пользователя.
    """
    text = (content or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError("Модель вернула ответ не в формате JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError("Не удалось разобрать ответ модели") from e
    if not isinstance(data, dict):
        raise LLMError("Модель вернула ответ не в формате JSON")
    return data


# ── C2/C3 — производные величины ─────────────────────────────────────────────

def atwater_kcal(m: Macros) -> float:
    """4·белки + 9·жиры + 4·углеводы — калорийность по составу."""
    return 4.0 * m.protein + 9.0 * m.fat + 4.0 * m.carbs


def atwater_delta(m: Macros) -> float:
    """C2: относительное расхождение заявленных ккал с расчётом по составу."""
    computed = max(atwater_kcal(m), 1.0)
    return abs(m.kcal - computed) / computed


def kj_ratio(m: Macros) -> float:
    """C3: отношение заявленных «ккал» к расчёту. ≈4.18 — записаны килоджоули."""
    return m.kcal / max(atwater_kcal(m), 1.0)


# ── C4.0 — контрольная сумма штрихкода ───────────────────────────────────────

def ean_checksum_ok(code: str | None) -> bool:
    """EAN-8/13/UPC-12: сходится ли контрольная цифра и не внутримагазинный ли код.

    Дешёвый детектор неверно прочитанных цифр: в замере источника
    (`off-source-probe-2026-07-28.md` §2.4) модель вернула штрихкод в 12 случаях
    из 25, из них 6 не прошли контрольную сумму и 2 оказались внутримагазинными
    весовыми кодами. Без проверки половина ушла бы в точный lookup и вернула бы
    ЧУЖОЙ товар с наивысшим доверием `match_confidence=exact`.
    """
    digits = (code or "").strip()
    if not digits.isdigit() or len(digits) not in (8, 12, 13):
        return False
    if len(digits) == 13 and digits[:2] in _IN_STORE_PREFIXES:
        return False
    body, check = digits[:-1], int(digits[-1])
    # Вес 3 — у цифр, стоящих справа налево на нечётных позициях (стандарт GS1).
    total = sum(d * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(int(c) for c in reversed(body)))
    return (10 - total % 10) % 10 == check


# ── C6 — сходимость граммовок с заявленным весом (dish-режимы) ───────────────

def weight_mismatch(
    portions: list[float | None],
    declared_weight_g: float | None,
    threshold: float | None = None,
) -> bool:
    """C6: |Σ portion_g_i − weight_g| / weight_g > порог.

    **Знаменатель — ЗАЯВЛЕННЫЙ вес, а не Σ граммовок** (ТЗ §6.4). Разница не
    косметическая: на наборе C-05 (Σ 500 г, заявлено 350 г) Σ-знаменатель даёт
    ровно 30.0% и не проходит строгое `>`, то есть положительный контроль молча
    не срабатывает. Ровно эту ошибку уже поймали в раннере при сборке яруса C —
    и поймал её не тест, а сверка процентов с источником (§3C.2 датасета).
    Реализации две (здесь и `tests/eval/run_eval.py`), поэтому пин формулы нужен
    в обеих: ошибка может повториться независимо.

    Усушка не моделируется намеренно: при выпаривании воды КБЖУ не теряется,
    теряется масса — поэтому расхождение это предупреждение, а не коррекция.
    """
    if not declared_weight_g or declared_weight_g <= 0:
        return False
    total = sum(float(p) for p in portions if p)
    if threshold is None:
        threshold = settings.check_weight_mismatch
    return abs(total - float(declared_weight_g)) / float(declared_weight_g) > threshold


# ── Свод: проверки одной позиции ─────────────────────────────────────────────

def run_checks(
    item: ExtractedItem,
    *,
    atwater: float | None = None,
    kj_lo: float | None = None,
    kj_hi: float | None = None,
    kcal_max: float | None = None,
    macros_sum_max: float | None = None,
    portion_g_max: float | None = None,
) -> CheckResult:
    """Прогоняет C2–C5 по позиции и возвращает флаги + возможное понижение readout.

    Никогда не бросает исключений и никогда не меняет КБЖУ (§2.2, §2.4).
    """
    atwater = settings.check_atwater if atwater is None else atwater
    kj_lo = settings.check_kj_lo if kj_lo is None else kj_lo
    kj_hi = settings.check_kj_hi if kj_hi is None else kj_hi
    kcal_max = settings.check_kcal_max if kcal_max is None else kcal_max
    macros_sum_max = (
        settings.check_macros_sum_max if macros_sum_max is None else macros_sum_max
    )
    portion_g_max = (
        settings.check_portion_g_max if portion_g_max is None else portion_g_max
    )

    flags: list[str] = []
    m = item.per100g

    if m is not None:
        macros_sum = m.protein + m.fat + m.carbs
        # C2 и C3 сравнивают заявленные ккал С СОСТАВОМ. Когда состава нет,
        # сравнивать не с чем: знаменатель вырождается в защитную единицу, и
        # ЛЮБОЕ kcal > 0 даёт «расхождение» в сотни процентов. Легитимный случай
        # ровно один и он известен — крепкий алкоголь (водка ≈ 235/0/0/0), под
        # который заведено правило C4.4. Не исключив вырожденный знаменатель,
        # мы понижали бы доверие к каждой бутылке и отправляли её в лестницу,
        # то есть C4.4 («это нормально») не имел бы никакого эффекта.
        # Цена честная: на позиции без макросов независимого сигнала нет вообще —
        # это и сообщает bounds_warn.
        macros_informative = macros_sum > 0.5

        if macros_informative:
            # C2 — сходимость по Атуотеру. Ловит грубый мусор; внутренне
            # согласованные ошибки (списано не из той колонки) не ловит и не
            # может — см. докстринг модуля.
            if atwater_delta(m) >= atwater:
                flags.append(FLAG_ARITH_MISMATCH)

            # C3 — кДж записаны как ккал. Полоса шире раннеровской (3.8–4.5),
            # потому что здесь знаменатель — оценка Атуотера со своей
            # погрешностью до 12%.
            if kj_lo <= kj_ratio(m) <= kj_hi:
                flags.append(FLAG_KJ_SUSPECTED)

        # C4.1/C4.2 — физические потолки. Сумма макросов > 102 на 100 г невозможна:
        # типовая причина — таблица «на порцию», объявленная таблицей «на 100 г».
        if m.kcal > kcal_max or macros_sum > macros_sum_max:
            flags.append(FLAG_BOUNDS_FAIL)
        elif not (0 <= m.protein <= 100 and 0 <= m.fat <= 100 and 0 <= m.carbs <= 100):
            flags.append(FLAG_BOUNDS_FAIL)
        # C4.3 — «ноль калорий» при заметном составе.
        elif m.kcal < 10 and macros_sum >= 5:
            flags.append(FLAG_BOUNDS_FAIL)
        # C4.4 — калории без состава. НЕ fail: крепкий алкоголь легитимен
        # (водка ≈ 235/0/0/0), поэтому предупреждение, а не понижение доверия.
        elif m.kcal >= 50 and macros_sum <= 0.5:
            flags.append(FLAG_BOUNDS_WARN)

    # C5 — порция. Типовая причина выхода за границы: путаница мл/г или выдумка.
    portion = item.portion_g
    if portion is not None and not (0 < portion <= portion_g_max):
        flags.append(FLAG_PORTION_WARN)
        portion = None

    # C4.0 — штрихкод. Не прошёл контроль → признака нет, а не «есть, но плохой»:
    # иначе он уйдёт в точный lookup и вернёт чужой товар с высшим доверием.
    barcode = item.barcode
    if barcode is not None and not ean_checksum_ok(barcode):
        flags.append(FLAG_BARCODE_INVALID)
        barcode = None

    downgraded = item.readout == "label" and bool(_DOWNGRADING.intersection(flags))
    return CheckResult(
        flags=flags,
        readout="estimate" if downgraded else item.readout,
        portion_g=portion,
        barcode=barcode,
        downgraded=downgraded,
    )


def apply_checks(item: ExtractedItem) -> ExtractedItem:
    """Позиция после проверок: тот же объект с флагами, маршрутом и без ложных признаков.

    Числа (`per100g`) переносятся ДОСЛОВНО — это и есть машинная формулировка
    принципа §2.4. Тест `test_checks_never_touch_numbers` следит за этим.
    """
    result = run_checks(item)
    return item.model_copy(
        update={
            "readout": result.readout,
            "portion_g": result.portion_g,
            "barcode": result.barcode,
            "warnings": [*item.warnings, *result.flags],
        }
    )
