"""tests/eval/test_scoring.py — негативные контроли метрик (llm-eval-dataset.md §3D.1).

Обычный `pytest`, без сети и платных вызовов.

**Правило, которое этот файл реализует:** метрика без хотя бы одного негативного
контроля не считается введённой. Причина — три замера подряд дали правдоподобные и
неверные числа (`nutrition-wave-plan.md` §5), и во всех трёх случаях результат был
внутренне согласован. На ярусе A портились данные, на B/C основной риск смещается на
сами метрики: метрика, пропускающая заведомо неверный ответ, даст бесполезное число,
неотличимое от полезного.

Поэтому здесь раннеру подсовывают синтетически испорченные ответы, и он **обязан их
забраковать**. Драйвятся настоящие функции раннера (`score_case`, `check_desync`,
`score_b_case`, `score_c_case`), а не копия их логики: тест копии проверяет копию.

Ключевой мутант — «две компенсирующие ошибки» (`test_m12_*`): один ингредиент ×2,
другой ×0.5, итог сходится точно при полностью неверном разборе. Именно из-за него
порог «итог ≤5%» из эскиза яруса C отменён (§3C.3). Если этот мутант проходит — ярус
C не измеряет то, что заявлено.

Мутанты B8, C4 и C9 ассертируются БЕЗ порога: их пороги фиксирует калибровочный
прогон (§7b шаг 6), которого ещё не было. Утверждение формулируется сравнением —
испорченный ответ обязан дать строго большее значение метрики, чем тот же ответ без
мутации.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.eval.run_eval import (  # noqa: E402
    C_WEIGHT_MISMATCH,
    c_expected_total,
    c_weight_mismatch,
    check_desync,
    score_b_case,
    score_c_case,
    score_case,
)

MACROS = ("kcal", "protein", "fat", "carbs")


# ── Синтетические данные ─────────────────────────────────────────────────────
# Ярусы B и C ещё не набраны. Гонять валидаторы «ни на чём» — это тест, который не
# может упасть, поэтому эталонные ответы строятся здесь и портятся мутациями.

def b_case() -> dict:
    """Взвешенный кейс яруса B: истина вычисляется из граммовок (§3B.3)."""
    return {
        "id": "B-01", "tier": "B", "subset": "B-w",
        "image": "images/B-01_weighed.jpg", "package_in_frame": False,
        "components": [
            {"name": "гречка отварная", "synonyms": ["гречка"], "g": 200,
             "per100g": {"kcal": 130.0, "protein": 4.5, "fat": 1.5, "carbs": 25.0},
             "ref_source": "USDA 20091, варёная"},
            {"name": "куриная грудка запечённая", "synonyms": ["курица"], "g": 100,
             "per100g": {"kcal": 165.0, "protein": 31.0, "fat": 3.6, "carbs": 0.0},
             "ref_source": "USDA 05064"},
        ],
        # Истина = 130·2 + 165·1 = 425 ккал. Коридор её накрывает.
        "corridor": {"sergei": {"kcal_lo": 380, "kcal_hi": 520}},
    }


def b_answer() -> list[dict]:
    """Идеальный ответ на кейс B: оба компонента, per100g как в истине."""
    return [
        {"name": "гречка отварная", "readout": "estimate", "portion_g": None,
         "per100g": {"kcal": 130.0, "protein": 4.5, "fat": 1.5, "carbs": 25.0}},
        {"name": "куриная грудка запечённая", "readout": "estimate", "portion_g": None,
         "per100g": {"kcal": 165.0, "protein": 31.0, "fat": 3.6, "carbs": 0.0}},
    ]


def a_index() -> dict:
    """Мини-ярус A под набор C.

    Вклады подобраны в отношении 1:2 НАМЕРЕННО — только при нём мутация
    «одну граммовку ×2, другую ×0.5» оставляет итог точно прежним
    (a + 2a = 3a  ==  2a + 0.5·2a = 3a). Без этого мутант 12 не воспроизводит
    тот дефект, ради которого заведён, и проверял бы что-то другое.
    """
    return {
        "A-90": {"id": "A-90", "tier": "A", "ground_truth": {
            "basis": "per100g", "name": "макароны сухие",
            "kcal": 100.0, "protein": 10.0, "fat": 5.0, "carbs": 20.0}},
        "A-91": {"id": "A-91", "tier": "A", "ground_truth": {
            "basis": "per100g", "name": "паста томатная",
            "kcal": 200.0, "protein": 20.0, "fat": 10.0, "carbs": 40.0}},
        "A-92": {"id": "A-92", "tier": "A", "ground_truth": {
            "basis": "per100g", "name": "сыр творожный",
            "kcal": 300.0, "protein": 8.0, "fat": 25.0, "carbs": 3.0}},
    }


def c_case(**over) -> dict:
    """Отрицательный контроль C6: Σ = 200 г, заявлено 190 → 5% < порога 0.30."""
    case = {
        "id": "C-90", "tier": "C", "dish_name": "Синтетическое блюдо",
        "declared_weight_g": 190, "expect_weight_mismatch": False,
        "ingredients": [{"case": "A-90", "g": 100}, {"case": "A-91", "g": 100}],
        "extra_images": [],
    }
    case["expected_total"] = c_expected_total(case, a_index())
    case.update(over)
    return case


def c_answer(case: dict, idx: dict) -> list[dict]:
    """Идеальный разбор набора: по позиции на ингредиент, per100g с этикеток."""
    out = []
    for ing in case["ingredients"]:
        gt = idx[ing["case"]]["ground_truth"]
        out.append({
            "name": gt["name"], "g": float(ing["g"]),
            "per100g": {k: gt[k] for k in MACROS},
        })
    return out


# ── Опорные точки: чистый ответ обязан быть зелёным ──────────────────────────
# Без них «мутант покраснел» ничего не значит — метрика могла краснеть всегда.

def test_baseline_b_is_green():
    m = score_b_case(b_case(), [b_answer(), b_answer(), b_answer()])
    assert m["b1_json_valid"]
    assert m["b2_no_label"]
    assert m["b3_portion_null"]
    assert m["b4_recall"] == 1.0
    assert m["b5_has_phantom"] is False
    assert m["b6_cv"] == 0.0
    assert m["b7_jaccard"] == 1.0
    assert m["b8_dev"] == 0.0
    assert m["b9"] == {"sergei": True}


def test_baseline_c_is_green():
    case, idx = c_case(), a_index()
    m = score_c_case(case, idx, c_answer(case, idx), dish_positions=1)
    assert m["c1_ok"] and m["c2_ok"] and m["c3_share"] == 1.0
    assert m["c4_dev"] == 0.0 and m["c4_ok"]
    assert m["c5_ok"] and m["c6_ok"] and m["c7_all_ingredients"] and m["c8_ok"]


# ── Мутант 1: ответ не парсится / не проходит схему → B1 ─────────────────────

def test_m1_broken_json_fails_b1():
    m = score_b_case(b_case(), [None, b_answer(), b_answer()])
    assert m["b1_json_valid"] is False
    # тот же класс на ярусе A — score_case обязан пометить провал, а не молчать
    assert score_case({"ground_truth": {"basis": "per100g", "kcal": 100}}, None) == {
        "json_valid": False
    }


# ── Мутанты 2-3: все kcal ×2 и ×0.5 → B8, C4, C9 (без порога, сравнением) ────

@pytest.mark.parametrize("factor", [2.0, 0.5])
def test_m2_m3_kcal_scaled_raises_b8(factor):
    base = score_b_case(b_case(), [b_answer()])
    bad = copy.deepcopy(b_answer())
    for it in bad:
        it["per100g"]["kcal"] *= factor
    got = score_b_case(b_case(), [bad])
    assert got["b8_dev"] > base["b8_dev"], (got["b8_dev"], base["b8_dev"])


@pytest.mark.parametrize("factor", [2.0, 0.5])
def test_m2_m3_kcal_scaled_raises_c4_and_c9(factor):
    case, idx = c_case(), a_index()
    base = score_c_case(case, idx, c_answer(case, idx))
    bad = c_answer(case, idx)
    for it in bad:
        it["per100g"]["kcal"] *= factor
    got = score_c_case(case, idx, bad)
    assert got["c4_dev"] > base["c4_dev"]
    assert got["c9_mean_dev"] > base["c9_mean_dev"]
    assert got["c3_share"] < 1.0   # C3 имеет порог и обязан покраснеть прямо


# ── Мутант 4: удалён компонент → B4, C1 ──────────────────────────────────────

def test_m4_missing_component_drops_b4():
    base = score_b_case(b_case(), [b_answer()])
    got = score_b_case(b_case(), [b_answer()[:1]])
    assert got["b4_recall"] < base["b4_recall"]
    # пропуск компонента штрафуется и через B8 — по построению формулы §3B.4
    assert got["b8_dev"] > base["b8_dev"]


def test_m4_missing_ingredient_fails_c1():
    case, idx = c_case(), a_index()
    got = score_c_case(case, idx, c_answer(case, idx)[:1])
    assert got["c1_ok"] is False
    assert got["c1_missing"] == ["A-91"]
    assert got["c7_all_ingredients"] is False


# ── Мутант 5: добавлен несуществующий компонент → B5, C1 ─────────────────────

def test_m5_phantom_component_raises_b5():
    bad = b_answer() + [{"name": "хлеб", "readout": "estimate", "portion_g": None,
                         "per100g": {"kcal": 250.0, "protein": 8.0,
                                     "fat": 3.0, "carbs": 48.0}}]
    got = score_b_case(b_case(), [bad])
    assert got["b5_has_phantom"] is True


def test_m5_phantom_ingredient_fails_c1():
    case, idx = c_case(), a_index()
    bad = c_answer(case, idx) + [
        {"name": "сыр творожный", "g": 30.0,
         "per100g": {k: idx["A-92"]["ground_truth"][k] for k in MACROS}}
    ]
    got = score_c_case(case, idx, bad)
    assert got["c1_ok"] is False
    assert got["c1_phantom"] == ["сыр творожный"]


# ── Мутант 6: readout подменён на "label" на кадре тарелки → B2 ──────────────

def test_m6_label_readout_on_plate_fails_b2():
    bad = copy.deepcopy(b_answer())
    bad[0]["readout"] = "label"
    got = score_b_case(b_case(), [bad])
    assert got["b2_no_label"] is False


# ── Мутант 7: portion_g=200 вместо null → B3 ────────────────────────────────

def test_m7_invented_portion_fails_b3():
    bad = copy.deepcopy(b_answer())
    bad[0]["portion_g"] = 200
    got = score_b_case(b_case(), [bad])
    assert got["b3_portion_null"] is False


# ── Мутант 8: три прогона с разбросом ×1.5 → B6, B7 ─────────────────────────
# Разброс ТОЛЬКО по величине не двигает B7 по построению (состав тот же), поэтому
# мутант разложен на две части. Иначе у B7 не было бы негативного контроля, а
# метрика без него не считается введённой — это ровно то, что проверяет §3D.1.

def test_m8_magnitude_spread_raises_b6():
    stable = score_b_case(b_case(), [b_answer(), b_answer(), b_answer()])
    drifted = copy.deepcopy(b_answer())
    for it in drifted:
        it["per100g"]["kcal"] *= 1.5
    got = score_b_case(b_case(), [b_answer(), b_answer(), drifted])
    assert got["b6_cv"] > stable["b6_cv"] == 0.0


def test_m8_composition_spread_drops_b7():
    stable = score_b_case(b_case(), [b_answer(), b_answer(), b_answer()])
    drifted = copy.deepcopy(b_answer())
    drifted[1]["name"] = "индейка запечённая"
    got = score_b_case(b_case(), [b_answer(), b_answer(), drifted])
    assert got["b7_jaccard"] < stable["b7_jaccard"] == 1.0


# ── Мутант 9: коридор разметчика сдвинут мимо вычисленной истины → B9 ────────

def test_m9_shifted_corridor_fails_b9():
    case = b_case()
    case["corridor"] = {"sergei": {"kcal_lo": 600, "kcal_hi": 800}}  # истина 425
    got = score_b_case(case, [b_answer()])
    assert got["b9"] == {"sergei": False}


# ── Мутант 10: граммовка взята от соседа → C2 ───────────────────────────────

def test_m10_swapped_grams_fail_c2():
    case, idx = c_case(declared_weight_g=190,
                       ingredients=[{"case": "A-90", "g": 120},
                                    {"case": "A-91", "g": 80}]), a_index()
    case["expected_total"] = c_expected_total(case, idx)
    bad = c_answer(case, idx)
    bad[0]["g"], bad[1]["g"] = bad[1]["g"], bad[0]["g"]   # граммовка соседа
    got = score_c_case(case, idx, bad)
    assert got["c2_ok"] is False
    assert got["c1_ok"] is True   # набор позиций верен — ловит именно C2


# ── Мутант 11: per100g одного ингредиента заменён на чужой → C3 ─────────────

def test_m11_foreign_per100g_fails_c3():
    case, idx = c_case(), a_index()
    bad = c_answer(case, idx)
    bad[0]["per100g"] = {k: idx["A-92"]["ground_truth"][k] for k in MACROS}
    got = score_c_case(case, idx, bad)
    assert got["c3_share"] < 1.0
    assert got["c3_max_dev"] > 0.02


# ── Мутант 12 (ключевой): один ×2, другой ×0.5 — итог сходится ──────────────

def test_m12_compensating_errors_pass_c4_but_fail_per_ingredient():
    """Две компенсирующие ошибки: итог сходится ТОЧНО при неверном разборе.

    Это тот самый класс «внутренне согласованного, но неверного числа», который в
    проекте трижды стоил перепрогонов. Метрика по итоговому числу его пропускает —
    и именно поэтому порог «итог ≤5%» отменён, а C оценивается поингредиентно.

    Тест утверждает ОБА факта сразу: C4 остаётся зелёным (иначе мутант не
    воспроизводит дефект) и C2 краснеет (иначе ярус C не измеряет заявленное).
    """
    case, idx = c_case(), a_index()
    bad = c_answer(case, idx)
    bad[0]["g"] *= 2      # вклад 1a → 2a
    bad[1]["g"] *= 0.5    # вклад 2a → 1a
    got = score_c_case(case, idx, bad)

    assert got["c4_dev"] == 0.0 and got["c4_ok"], "мутант не воспроизводит дефект"
    assert got["c2_ok"] is False, "поингредиентная проверка обязана ловить то, что итог маскирует"


# ── Мутант 13: dish-режим вернул две итоговые позиции → C5 ──────────────────

def test_m13_two_dish_positions_fail_c5():
    case, idx = c_case(), a_index()
    got = score_c_case(case, idx, c_answer(case, idx), dish_positions=2)
    assert got["c5_ok"] is False


# ── Мутант 14: ответ второго батча отброшен целиком → C7 ───────────────────

def test_m14_dropped_batch_fails_c7():
    """Набор из 6 ингредиентов при лимите 5 фото — на границе батча теряется хвост."""
    idx = a_index()
    idx.update({
        f"A-9{n}": {"id": f"A-9{n}", "tier": "A", "ground_truth": {
            "basis": "per100g", "name": f"продукт {n}",
            "kcal": 50.0 * n, "protein": 1.0 * n, "fat": 2.0 * n, "carbs": 3.0 * n}}
        for n in (3, 4, 5)
    })
    case = c_case(
        ingredients=[{"case": cid, "g": 50} for cid in
                     ("A-90", "A-91", "A-92", "A-93", "A-94", "A-95")],
        declared_weight_g=300,
    )
    case["expected_total"] = c_expected_total(case, idx)
    full = c_answer(case, idx)
    assert score_c_case(case, idx, full)["c7_all_ingredients"] is True

    got = score_c_case(case, idx, full[:5])   # второй батч отброшен
    assert got["c7_all_ingredients"] is False
    assert got["c1_missing"] == ["A-95"]


# ── Мутант 15: повторный товар отдан двумя позициями → C8 ──────────────────

def test_m15_duplicate_product_fails_c8():
    case, idx = c_case(), a_index()
    bad = c_answer(case, idx)
    dup = copy.deepcopy(bad[0])
    bad.append(dup)                       # тот же товар со второго кадра
    got = score_c_case(case, idx, bad)
    assert got["c8_ok"] is False
    # §3C.2: отчёт обязан различать «дедуп не сработал» и «имена разошлись»,
    # иначе метрика обвинит не тот механизм.
    assert got["c8_names_normalized_equal"] == {"A-90": True}

    # Регистр и лишние пробелы нормализация снимает — это ещё «дедуп не сработал».
    bad[-1]["name"] = "Макароны  СУХИЕ"
    assert score_c_case(case, idx, bad)["c8_names_normalized_equal"] == {"A-90": True}

    # А порядок слов — уже нет (§5.5 ТЗ нормализует только регистр и пробелы).
    # Метрика обязана показать, что виноват НЕ дедуп, иначе обвинит не тот механизм.
    bad[-1]["name"] = "сухие макароны"
    got = score_c_case(case, idx, bad)
    assert got["c8_ok"] is False
    assert got["c8_names_normalized_equal"] == {"A-90": False}


# ── Мутант 16: в положительном контроле убрано расхождение → C6+ ───────────

def test_m16_removed_mismatch_kills_positive_control():
    """Набор-положительный-контроль: заявленный вес далеко от Σ граммовок."""
    case = c_case(declared_weight_g=350, expect_weight_mismatch=True)  # Σ=200 → 75%
    assert c_weight_mismatch(case) is True
    assert score_c_case(case, a_index(), c_answer(case, a_index()))["c6_ok"]

    case["declared_weight_g"] = 200      # расхождение убрано
    assert c_weight_mismatch(case) is False
    got = score_c_case(case, a_index(), c_answer(case, a_index()))
    assert got["c6_ok"] is False, "обязательное срабатывание пропало и осталось незамеченным"


# ── Мутант 17: в отрицательном контроле вес сдвинут за порог → C6− ─────────

def test_m17_shifted_weight_breaks_negative_control():
    case, idx = c_case(), a_index()                 # Σ=200, заявлено 190 → 5.3%
    assert c_weight_mismatch(case) is False
    assert score_c_case(case, idx, c_answer(case, idx))["c6_ok"]

    case["declared_weight_g"] = 300                 # |200−300|/300 = 33.3% > 0.30
    got = score_c_case(case, idx, c_answer(case, idx))
    assert got["c6_flag"] is True
    assert got["c6_ok"] is False, "молчание отрицательного контроля нарушено"


def test_c6_denominator_is_declared_weight_not_sum():
    """Знаменатель C6 — заявленный вес блюда (ТЗ контракта §6.4), а не Σ граммовок.

    Пин формулы, а не поведения. Обе версии совпадают почти везде и расходятся у
    границы — на реальном C-05 (Σ 500, заявлено 350) Σ-знаменатель даёт ровно
    30.0%, что НЕ проходит строгое `>`, и обязательное срабатывание исчезает без
    единого красного теста. Первая реализация была именно такой.
    """
    near = {"declared_weight_g": 350,
            "ingredients": [{"case": "A-90", "g": 500}]}     # 150/350=42.9% vs 150/500=30.0%
    assert c_weight_mismatch(near) is True

    # И зеркальный случай: Σ-знаменатель сработал бы там, где ТЗ велит молчать.
    other = {"declared_weight_g": 280,
             "ingredients": [{"case": "A-90", "g": 200}]}    # 80/280=28.6% vs 80/200=40%
    assert c_weight_mismatch(other) is False


def test_c6_threshold_is_actually_exercised():
    """Отрицательные контроли обязаны стоять «около, но под порогом» (§3C.2 п.1).

    Заявить вес ровно равным Σ граммовок — значит не проверить порог 0.30 вовсе.
    """
    case = c_case()
    total_g = sum(i["g"] for i in case["ingredients"])
    ratio = abs(total_g - case["declared_weight_g"]) / case["declared_weight_g"]
    assert 0 < ratio < C_WEIGHT_MISMATCH


# ── Ярус A: детектор рассинхрона обязан ловить подмену снимка ───────────────

def test_check_desync_catches_swapped_photo():
    """Негативный контроль для check_desync — проверки §7a, а не §3D.1.

    Здесь эталоны разъезжаются с содержимым снимка: модель на кейсе A-90 читает
    продукт, чей эталон лежит в A-91. Метрики этого не видят (для них «модель
    ошиблась» и «эталон не от того снимка» неразличимы) — именно поэтому детектор
    печатается ДО метрик.
    """
    idx = a_index()
    cases = [dict(v, image=f"images/{k}.jpg") for k, v in idx.items()]
    # ответ на A-90 — это на самом деле продукт из A-91
    records = [{"id": "A-90", "runs": [{"items": [{
        "name": "паста томатная",
        "per100g": {k: idx["A-91"]["ground_truth"][k] for k in MACROS},
    }]}]}]
    alerts = check_desync(cases, records)
    assert alerts, "подмена снимка прошла незамеченной"
    assert alerts[0]["strong"] is True
    assert alerts[0]["name_match"] == "A-91"


def test_check_desync_silent_on_clean_answers():
    """Контроль на ложные срабатывания: верный ответ детектор трогать не должен."""
    idx = a_index()
    cases = [dict(v, image=f"images/{k}.jpg") for k, v in idx.items()]
    records = [{"id": cid, "runs": [{"items": [{
        "name": idx[cid]["ground_truth"]["name"],
        "per100g": {k: idx[cid]["ground_truth"][k] for k in MACROS},
    }]}]} for cid in idx]
    assert check_desync(cases, records) == []
