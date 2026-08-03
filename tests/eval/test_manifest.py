"""tests/eval/test_manifest.py — целостность eval-датасета (llm-eval-dataset.md §3D.5).

Обычный `pytest`, без сети и платных вызовов (решение D4: отдельный CI не заводим).

Зачем это существует. Рассинхрон «фото ↔ эталон» в 11 кейсах яруса A родился не при
съёмке, а при ручной перенумерации — то есть при действии, которое нечем было
проверить (§7a). Валидаторы ниже проверяют ровно те инварианты, которые то действие
нарушило: сироты среди файлов и записей, дубли ID, самосогласованность эталона.

Порядок из §7b: инструмент проверки заводится ДО данных. Поэтому ярусы B и C сейчас
пусты, и валидаторы по ним не находят ни одного кейса. Такой тест зелёный ни на чём и
не введён. Отсюда правило файла: **у каждого валидатора есть синтетическая фикстура —
заведомо корректный кейс и заведомо битые его варианты, на которых он обязан упасть.**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.eval.run_eval import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    PROVIDERS,
    c_expected_total,
)

EVAL_DIR = Path(__file__).resolve().parent
MANIFEST = EVAL_DIR / "manifest.yaml"
IMAGES = EVAL_DIR / "images"

ALLOWED_STATUS = {"missing_photo", "unscorable"}
ALLOWED_TIERS = {"A", "B", "C"}
ALLOWED_SUBSETS = {"B-w", "B-s", "B-m", "B-a"}
MACROS = ("kcal", "protein", "fat", "carbs")

# Файлы в images/, которым НЕ обязано соответствовать поле image: в манифесте.
# Список явный: без него правило «каждый файл упомянут» красное с первого запуска,
# а с неявными исключениями оно перестаёт что-либо гарантировать.
#
# `_quarantine/` из списка УБРАН 2026-08-01 (§3D.5 оговаривал исключение «до
# заведения C-04»): оба карантинных кадра теперь адресуются из `extra_images`
# набора C-04 как второй ракурс товара, то есть перестали быть вне зачёта.
# Держать исключение дальше значило бы разрешить копить там что угодно.
#
# `_field/` ДОБАВЛЕН 2026-08-03. Это не возврат к «складу вне зачёта»: полевые
# кадры (снимки, на которых ошибся прод) не являются данными манифеста в принципе
# — у них нет эталона, они не входят ни в один ярус и не участвуют в метриках.
# Требовать на них запись в манифесте значило бы либо размывать замороженный
# baseline яруса A, либо заводить фиктивные записи. Инвариант «ничего не копится
# без объяснения» при этом сохранён другой проверкой — `check_field_documented`:
# каталог обязан иметь README.md, а каждый файл в нём — быть в README упомянут.
IMAGE_EXEMPT_DIRS: tuple[str, ...] = ("_field",)
IMAGE_EXEMPT_NAMES = (".gitkeep",)

# §3D.5 п.3: порог Атуотера здесь 15%, а НЕ 12% из ТЗ контракта §6.2.
# Фактический максимум на активных кейсах — A-30 (печенье, клетчатка 24 г): 11.97%,
# то есть 0.03 п.п. до ложного срабатывания. Клетчатка, полиолы и округление
# производителем дают легальное расхождение; опечатка в макросе сдвигает Атуотера
# на десятки процентов, так что чувствительность к тому, ради чего проверка нужна,
# сохраняется.
ATWATER_TOL = 0.15
EXPECTED_TOTAL_TOL = 0.05  # §3C.6: два эталона падают ровно на «.x5» → не побитово


def load_manifest() -> list[dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return [c for c in data if isinstance(c, dict) and "id" in c]


# ── Валидаторы (§3D.5). Возвращают список нарушений, не бросают ───────────────

def check_schema(cases: list[dict]) -> list[str]:
    """§3D.5 п.1 — схема по ярусам, дубли ID, допустимые значения status."""
    errs: list[str] = []
    seen: set[str] = set()
    for c in cases:
        cid = c.get("id")
        if cid in seen:
            errs.append(f"{cid}: дубль ID")
        seen.add(cid)

        tier = c.get("tier")
        if tier not in ALLOWED_TIERS:
            errs.append(f"{cid}: неизвестный tier {tier!r}")
        status = c.get("status")
        if status is not None and status not in ALLOWED_STATUS:
            errs.append(f"{cid}: недопустимый status {status!r}")

        if tier == "A":
            gt = c.get("ground_truth")
            if not isinstance(gt, dict):
                errs.append(f"{cid}: ярус A без ground_truth")
                continue
            if gt.get("basis") not in ("per100g", "per_portion"):
                errs.append(f"{cid}: basis {gt.get('basis')!r} вне {{per100g, per_portion}}")
            if gt.get("basis") == "per_portion" and not gt.get("portion_grams"):
                errs.append(f"{cid}: per_portion без portion_grams — не привести к 100 г")
            if gt.get("kcal") is None:
                errs.append(f"{cid}: не заполнен kcal")
            if not gt.get("name"):
                errs.append(f"{cid}: не заполнено name")
            if status is None and not c.get("image"):
                errs.append(f"{cid}: активный кейс без image")
        elif tier == "B":
            if c.get("subset") not in ALLOWED_SUBSETS:
                errs.append(f"{cid}: subset {c.get('subset')!r} вне {sorted(ALLOWED_SUBSETS)}")
            if not c.get("image"):
                errs.append(f"{cid}: ярус B без image")
            if not c.get("components"):
                errs.append(f"{cid}: ярус B без components")
        elif tier == "C":
            if not c.get("dish_name"):
                errs.append(f"{cid}: набор C без dish_name")
            if c.get("declared_weight_g") is None:
                errs.append(f"{cid}: набор C без declared_weight_g")
            if not c.get("ingredients"):
                errs.append(f"{cid}: набор C без ingredients")
            if not isinstance(c.get("expected_total"), dict):
                errs.append(f"{cid}: набор C без expected_total")
    return errs


def check_images(cases: list[dict], images_dir: Path) -> list[str]:
    """§3D.5 п.2 — нет ни файлов-сирот, ни записей-сирот.

    Оба вида сирот появляются именно при ручной перенумерации — том действии,
    которое породило рассинхрон 11 кейсов.
    """
    errs: list[str] = []
    referenced: set[Path] = set()
    for c in cases:
        for rel in [c.get("image")] + list(c.get("extra_images") or []):
            if not rel:
                continue
            p = (images_dir.parent / rel).resolve()
            if not p.exists():
                errs.append(f"{c['id']}: файла нет — {rel}")
            referenced.add(p)

    if not images_dir.exists():
        return errs
    for p in images_dir.rglob("*"):
        if p.is_dir():
            continue
        if p.name in IMAGE_EXEMPT_NAMES:
            continue
        if any(part in IMAGE_EXEMPT_DIRS for part in p.relative_to(images_dir).parts):
            continue
        if p.resolve() not in referenced:
            errs.append(f"файл-сирота: {p.relative_to(images_dir.parent)} не упомянут в манифесте")
    return errs


def check_field_documented(images_dir: Path) -> list[str]:
    """Полевые кадры вне манифеста — но не вне учёта.

    `_field/` исключён из `check_images` (см. комментарий у `IMAGE_EXEMPT_DIRS`),
    поэтому правило «каждый файл кем-то упомянут» для него держит эта проверка:
    каталог обязан иметь README.md, и каждый кадр обязан быть в нём назван.
    Без этого исключение выродилось бы в «сюда можно класть что угодно» — ровно
    то, из-за чего 2026-08-01 убрали исключение для `_quarantine/`.
    """
    field = images_dir / "_field"
    if not field.exists():
        return []
    readme = field / "README.md"
    if not readme.exists():
        return [f"{field.name}/: нет README.md — полевые кадры обязаны быть описаны"]
    text = readme.read_text(encoding="utf-8")
    errs = []
    for p in sorted(field.iterdir()):
        if p.is_dir() or p.name == "README.md":
            continue
        if p.name not in text:
            errs.append(f"{field.name}/{p.name}: файл не упомянут в README.md")
    return errs


def atwater_dev(gt: dict) -> float | None:
    """Относительное расхождение kcal с формулой Атуотера (4/9/4). None — не считаем.

    Знаменатель — ВЫЧИСЛЕННОЕ значение, а не напечатанное: та же формула, что у
    проверки C2 (ТЗ контракта §6.2, `delta = |kcal − computed| / max(computed, 1)`).
    Совпадение важно не из аккуратности: числа «A-30 = 11.97%, 0.03 п.п. до порога
    12%» из §3D.5 обоснованы именно этим знаменателем, и от другого разъедутся.
    """
    kcal = gt.get("kcal")
    if kcal is None:
        return None
    p, f, c = gt.get("protein"), gt.get("fat"), gt.get("carbs")
    if p is None or f is None or c is None:
        return None  # неполный эталон — Атуотер неопределён, ловится отдельной проверкой
    computed = 4 * float(p) + 9 * float(f) + 4 * float(c)
    return abs(float(kcal) - computed) / max(computed, 1.0)


def check_atwater(cases: list[dict], tol: float = ATWATER_TOL) -> list[str]:
    """§3D.5 п.3 — самосогласованность эталонов яруса A.

    ВАЖНО, чему проверка НЕ равна: она подтверждает согласованность чисел между
    собой, но не их привязку к снимку. Ровно на этом обожглись в §9 — набор цифр
    сходился по Атуотеру и был при этом неверным. От подмены содержимого работает
    только консенсусный check_desync().
    """
    errs = []
    for c in cases:
        if c.get("tier") != "A" or c.get("status"):
            continue
        dev = atwater_dev(c.get("ground_truth") or {})
        if dev is not None and dev > tol:
            errs.append(f"{c['id']}: Атуотер расходится на {dev * 100:.1f}% (> {tol * 100:.0f}%)")
    return errs


def check_c_links(cases: list[dict], tol: float = EXPECTED_TOTAL_TOL) -> list[str]:
    """§3D.5 п.4 / §3C.6 — expected_total пересчитывается из ТЕКУЩИХ записей A.

    Хранимое значение протухнет молча, если поправить эталон A; вычисляемое
    не с чем сравнивать. Поэтому хранятся оба и сверяются с допуском ±0.05
    (два эталона попадают ровно на «.x5» и зависят от режима округления).
    """
    a_index = {c["id"]: c for c in cases if c.get("tier") == "A"}
    errs = []
    for c in cases:
        if c.get("tier") != "C":
            continue
        try:
            recomputed = c_expected_total(c, a_index)
        except (KeyError, ValueError) as e:
            errs.append(f"{c['id']}: пересчёт невозможен — {e}")
            continue
        stored = c.get("expected_total") or {}
        for k in MACROS:
            if stored.get(k) is None:
                errs.append(f"{c['id']}: в expected_total нет {k}")
                continue
            # round() перед сравнением — иначе 12.0 − 11.95 даёт 0.0500000000000007
            # и допуск «ровно ±0.05» срабатывает на том, ради чего он и введён.
            if round(abs(float(stored[k]) - recomputed[k]), 6) > tol:
                errs.append(
                    f"{c['id']}: expected_total.{k} = {stored[k]}, пересчёт даёт "
                    f"{recomputed[k]} — эталон яруса A поменялся, истина набора уехала"
                )
    return errs


def check_c_ingredients(cases: list[dict]) -> list[str]:
    """§3D.5 п.5 — пригодность кейсов A, на которые ссылается набор C.

    Не теоретическая проверка: в черновые наборы попали A-17/A-26/A-29 с
    незаполненным макросом, и вычисляемый эталон оказался неопределён.
    """
    a_index = {c["id"]: c for c in cases if c.get("tier") == "A"}
    errs = []
    for c in cases:
        if c.get("tier") != "C":
            continue
        for ing in c.get("ingredients") or []:
            ref = a_index.get(ing.get("case"))
            if ref is None:
                errs.append(f"{c['id']}: ссылка на несуществующий кейс {ing.get('case')}")
                continue
            if ref.get("status"):
                errs.append(
                    f"{c['id']}: {ref['id']} имеет status={ref['status']} — в наборы не годится"
                )
            gt = ref.get("ground_truth") or {}
            if gt.get("basis") != "per100g":
                errs.append(f"{c['id']}: {ref['id']} basis={gt.get('basis')}, нужен per100g")
            for k in MACROS:
                if gt.get(k) is None:
                    errs.append(f"{c['id']}: у {ref['id']} не заполнен макрос {k}")
            # §3C.2 п.3: состав для dish_text формулируется явно, а не берётся с
            # этикетки. «Макароны "Спиральки-экспресс"» не сообщают, что продукт
            # сухой, и модель отдаст per100g варёных — метрика C9 запишет верное
            # поведение как промах втрое. Требуем поле, а не полагаемся на заметку.
            if not (ing.get("text_name") or "").strip():
                errs.append(
                    f"{c['id']}: у ингредиента {ing.get('case')} нет text_name — "
                    "состав для dish_text не сформулирован"
                )
    return errs


def check_b_fields(cases: list[dict]) -> list[str]:
    """§3D.5 п.6 — package_in_frame=false у всех B; у B-w заполнены g, per100g, источник."""
    errs = []
    for c in cases:
        if c.get("tier") != "B":
            continue
        if c.get("package_in_frame") is not False:
            errs.append(
                f"{c['id']}: package_in_frame={c.get('package_in_frame')!r} — "
                'при упаковке в кадре readout="label" законен и метрика B2 мертва'
            )
        if c.get("subset") != "B-w":
            continue
        for comp in c.get("components") or []:
            nm = comp.get("name")
            if comp.get("g") is None:
                errs.append(f"{c['id']}/{nm}: B-w без взвешенной граммовки g")
            per = comp.get("per100g") or {}
            missing = [k for k in MACROS if per.get(k) is None]
            if missing:
                errs.append(f"{c['id']}/{nm}: B-w без per100g {missing}")
            if not comp.get("ref_source") and not comp.get("case"):
                errs.append(
                    f"{c['id']}/{nm}: источник per100g не записан (ref_source либо case) — "
                    "эталон без источника неаудируем"
                )
    return errs


ALL_CHECKS = (
    check_schema,
    check_images,
    check_atwater,
    check_c_links,
    check_c_ingredients,
    check_b_fields,
)


# ── Прогон валидаторов на реальном манифесте ─────────────────────────────────

@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return load_manifest()


def test_schema_real(cases):
    assert check_schema(cases) == []


def test_images_real(cases):
    assert check_images(cases, IMAGES) == []


def test_field_documented_real():
    assert check_field_documented(IMAGES) == []


def test_atwater_real(cases):
    assert check_atwater(cases) == []


def test_c_links_real(cases):
    assert check_c_links(cases) == []


def test_c_ingredients_real(cases):
    assert check_c_ingredients(cases) == []


def test_b_fields_real(cases):
    assert check_b_fields(cases) == []


def test_tier_a_frozen_at_25_scorable(cases):
    """Ярус A заморожен как регрессионный baseline: 25 активных кейсов (§7c).

    Прибавление кейса делает порог «не хуже 92%» неопределённым, поэтому число
    зафиксировано числом, а не намерением.
    """
    active = [c for c in cases if c.get("tier") == "A" and not c.get("status")]
    assert len(active) == 25, [c["id"] for c in active]


def test_tier_c_is_assembled(cases):
    """Ярус C собран целиком — 5 наборов (§7b шаг 3, блокер волны 1)."""
    sets = [c for c in cases if c.get("tier") == "C"]
    assert len(sets) == 5, [c["id"] for c in sets]


def test_tier_c_weight_controls_are_meaningful(cases):
    """§3C.2 п.1 — отрицательные контроли стоят «около, но под порогом».

    Заявить вес ровно равным Σ граммовок значит не проверить порог 0.30 вовсе.
    Проверяем и то, что положительный контроль ровно один: два обязательных
    срабатывания сделали бы неотличимыми «C6 работает» и «C6 всегда true».
    """
    from tests.eval.run_eval import C_WEIGHT_MISMATCH, c_weight_mismatch

    sets = [c for c in cases if c.get("tier") == "C"]
    positives = [c for c in sets if c.get("expect_weight_mismatch")]
    assert len(positives) == 1, [c["id"] for c in positives]

    for c in sets:
        total_g = sum(float(i["g"]) for i in c["ingredients"])
        ratio = abs(total_g - float(c["declared_weight_g"])) / float(c["declared_weight_g"])
        assert c_weight_mismatch(c) is bool(c["expect_weight_mismatch"]), (c["id"], ratio)
        if not c["expect_weight_mismatch"]:
            # не ноль (порог нагружен) и не впритык к нему (случайно не переедет)
            assert 0.03 < ratio < C_WEIGHT_MISMATCH - 0.10, (c["id"], ratio)
        else:
            assert ratio > C_WEIGHT_MISMATCH + 0.10, (c["id"], ratio)


def test_tier_c_sets_are_separable(cases):
    """§3C.2 п.2 — наборы различаются по итогу МНОГО сильнее допуска C4 (0.5%).

    Требование не «различаться в разы», а «различаться много больше допуска», и оно
    проверяемо. В первой редакции C-04 копировал состав C-03 с теми же граммовками:
    эталоны совпадали точно, и требование нарушалось ровно для той пары, которую
    вероятнее всего перепутать.
    """
    from tests.eval.run_eval import C_TOTAL_TOL

    totals = {c["id"]: float(c["expected_total"]["kcal"])
              for c in cases if c.get("tier") == "C"}
    ordered = sorted(totals, key=totals.get)
    gaps = {(a, b): abs(totals[b] - totals[a]) / totals[a]
            for a, b in zip(ordered, ordered[1:])}
    worst = min(gaps, key=gaps.get)
    assert gaps[worst] > C_TOTAL_TOL * 10, (worst, gaps[worst])


def test_tier_c_batch_and_dedup_material_present(cases):
    """У яруса C есть чем нагрузить батчи (C7) и дедуп через границу батча (C8).

    Оба свойства выражены числом кадров, а не намерением: если `llm_max_images`
    когда-нибудь поднимут до 10 (блок 6 поднимает кап фронта), набор перестанет
    быть тестом батчей молча — и тест это покажет.
    """
    from app.config import settings

    sets = {c["id"]: c for c in cases if c.get("tier") == "C"}
    photos = {cid: len(c["ingredients"]) + len(c.get("extra_images") or [])
              for cid, c in sets.items()}

    over = [cid for cid, n in photos.items() if n > settings.llm_max_images]
    assert over, (photos, settings.llm_max_images)

    dedup = [c for c in sets.values() if c.get("extra_images")]
    assert dedup, "нет набора с повторным кадром товара — C8 непроверяем"
    for c in dedup:
        n = len(c["ingredients"]) + len(c["extra_images"])
        assert n > settings.llm_max_images, (c["id"], n)
        # повтор обязан быть кадром товара, который в наборе УЖЕ есть, — иначе это
        # не дедуп, а лишний ингредиент
        refs = {i["case"] for i in c["ingredients"]}
        assert refs, c["id"]


def test_eval_default_matches_prod_target():
    """Предусловие ТЗ контракта §0.1: дефолт раннера == прод-цель.

    Пока они расходятся, промпт настраивается вслепую — это и есть причина, по
    которой блок 1 идёт первым. Проверяем сходимость всех параметров вызова, а не
    только имени модели: расхождение по `max_tokens_param` или `reasoning_effort`
    так же делает замер несопоставимым с продом, но заметно куда позже.
    """
    from app.config import settings

    prov = PROVIDERS[DEFAULT_PROVIDER]
    assert DEFAULT_MODEL == settings.llm_vision_model
    assert prov.base_url == settings.llm_base_url
    assert prov.max_images == settings.llm_max_images
    assert prov.max_tokens_param == settings.llm_max_tokens_param
    assert prov.reasoning_effort == settings.llm_reasoning_effort
    # КЛЮЧ — такой же параметр прод-цели, и его здесь не хватало. Цена пропуска
    # (найдено 2026-08-03 на VPS): прод ходит по `LLM_API_KEY`, а раннер требовал
    # `OPENAI_API_KEY` — на машине с КОРРЕКТНЫМ прод-`.env` замер не запускался
    # вовсе, и сообщение отправляло чинить `.env` там, где чинить было нечего.
    # Сверяем не значение (в CI его нет), а то, что прод-ключ вообще принимается.
    assert "llm_api_key" in prov.key_attrs


# ── Синтетические фикстуры: доказательство, что валидаторы умеют падать ───────
# Ярусы B и C пусты, поэтому проверки по ним на реальном манифесте проходят ни на
# чём. Ниже — заведомо корректные кейс B и набор C плюс их испорченные варианты.

def _a_case(cid: str, **over) -> dict:
    gt = {"basis": "per100g", "kcal": 100.0, "protein": 10.0, "fat": 4.0,
          "carbs": 5.0, "name": f"продукт {cid}"}
    gt.update(over.pop("ground_truth", {}))
    case = {"id": cid, "tier": "A", "image": f"images/{cid}_clean.jpg",
            "trap": "clean", "ground_truth": gt}
    case.update(over)
    return case


def _b_case(**over) -> dict:
    case = {
        "id": "B-01", "tier": "B", "subset": "B-w",
        "image": "images/B-01_weighed.jpg", "package_in_frame": False,
        "components": [
            {"name": "гречка отварная", "synonyms": ["гречка"], "g": 180,
             "per100g": {"kcal": 132, "protein": 4.5, "fat": 1.6, "carbs": 25.0},
             "ref_source": "USDA 20091, варёная"},
            {"name": "куриная грудка запечённая", "synonyms": ["курица"], "g": 120,
             "per100g": {"kcal": 165, "protein": 31.0, "fat": 3.6, "carbs": 0.0},
             "ref_source": "USDA 05064"},
        ],
        "corridor": {"sergei": {"kcal_lo": 380, "kcal_hi": 520}},
    }
    case.update(over)
    return case


def _c_fixture() -> list[dict]:
    """Ярус A (2 кейса) + корректный набор C, ссылающийся на них."""
    a1 = _a_case("A-90", ground_truth={"kcal": 100.0, "protein": 10.0,
                                       "fat": 4.0, "carbs": 5.0})
    a2 = _a_case("A-91", ground_truth={"kcal": 200.0, "protein": 20.0,
                                       "fat": 8.0, "carbs": 10.0})
    c = {
        "id": "C-90", "tier": "C", "dish_name": "Синтетическое блюдо",
        "declared_weight_g": 190,   # Σ = 200 → 5% < 0.30, C6 обязан молчать
        "expect_weight_mismatch": False,
        "ingredients": [
            {"case": "A-90", "g": 100, "text_name": "продукт девяносто"},
            {"case": "A-91", "g": 100, "text_name": "продукт девяносто один"},
        ],
        "extra_images": [],
        "expected_total": {"kcal": 300.0, "protein": 30.0, "fat": 12.0, "carbs": 15.0},
    }
    return [a1, a2, c]


def test_fixtures_are_valid_baseline(tmp_path):
    """Опорная точка: сами фикстуры чистые. Иначе «валидатор упал» ничего не значит."""
    images = tmp_path / "images"
    images.mkdir()
    cases = _c_fixture() + [_b_case()]
    for c in cases:
        if c.get("image"):
            (tmp_path / c["image"]).write_bytes(b"x")
    for fn in ALL_CHECKS:
        errs = fn(cases, images) if fn is check_images else fn(cases)
        assert errs == [], f"{fn.__name__}: {errs}"


@pytest.mark.parametrize(
    "mutate, expect_in",
    [
        # схема
        (lambda cs: cs.append(_a_case("A-90")), "дубль ID"),
        (lambda cs: cs[0].update(tier="Z"), "неизвестный tier"),
        (lambda cs: cs[0].update(status="потерялось"), "недопустимый status"),
        (lambda cs: cs[0]["ground_truth"].update(basis="per_serving"), "basis"),
        (lambda cs: cs[0]["ground_truth"].update(basis="per_portion"), "portion_grams"),
        (lambda cs: cs[0].update(image=None), "без image"),
        (lambda cs: cs[-1].update(subset="B-x"), "subset"),
        (lambda cs: cs[2].update(ingredients=[]), "без ingredients"),
        (lambda cs: cs[2].update(expected_total=None), "без expected_total"),
    ],
)
def test_check_schema_catches(mutate, expect_in):
    cases = _c_fixture() + [_b_case()]
    mutate(cases)
    errs = check_schema(cases)
    assert any(expect_in in e for e in errs), errs


def test_check_images_catches_missing_file(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    cases = _c_fixture()
    # ни одного файла на диске не создаём → все image: — сироты-записи
    errs = check_images(cases, images)
    assert any("файла нет" in e for e in errs), errs


def test_check_images_catches_orphan_file(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    cases = _c_fixture()
    for c in cases:
        if c.get("image"):
            (tmp_path / c["image"]).write_bytes(b"x")
    (images / "A-99_forgotten.jpg").write_bytes(b"x")
    errs = check_images(cases, images)
    assert any("файл-сирота" in e for e in errs), errs


def test_check_images_exemptions_are_explicit(tmp_path):
    """Исключение ровно одно — `.gitkeep`. Карантин больше не исключён.

    Незанятый кадр в `_quarantine/` обязан краснеть: с 2026-08-01 такие кадры
    адресуются из `extra_images` набора C-04, и «папка, куда можно складывать
    что угодно» перестала быть частью схемы.
    """
    images = tmp_path / "images"
    (images / "_quarantine").mkdir(parents=True)
    cases = _c_fixture()
    for c in cases:
        if c.get("image"):
            (tmp_path / c["image"]).write_bytes(b"x")
    (images / ".gitkeep").write_bytes(b"")
    assert check_images(cases, images) == []

    (images / "_quarantine" / "A-90_dup.jpg").write_bytes(b"x")
    assert any("файл-сирота" in e for e in check_images(cases, images))

    # ...но тот же кадр, адресованный из extra_images, снова легален.
    cases[2]["extra_images"] = ["images/_quarantine/A-90_dup.jpg"]
    assert check_images(cases, images) == []


def test_check_atwater_catches_typo():
    cases = _c_fixture()
    # опечатка в макросе: белки 10 → 40 сдвигают Атуотера на десятки процентов
    cases[0]["ground_truth"]["protein"] = 40.0
    errs = check_atwater(cases)
    assert any("A-90" in e and "Атуотер" in e for e in errs), errs


def test_atwater_threshold_is_justified_by_data(cases):
    """Порог 15%, а не 12% — обоснование числами, а не намерением (§3D.5 п.3).

    A-30 (печенье, клетчатка 24 г) — фактический максимум среди активных кейсов:
    11.97%, то есть **0.03 п.п.** до порога 12% из ТЗ контракта §6.2. Клетчатка,
    полиолы и округление производителем дают легальное расхождение, поэтому 12%
    здесь красил бы верный эталон при первой же правке.

    Тест защищает и обратное: 15% не должно стать «порогом, который проходит всё» —
    максимум обязан оставаться заметно ниже, иначе проверка выродилась.
    """
    active = [c for c in cases if c.get("tier") == "A" and not c.get("status")]
    devs = {c["id"]: atwater_dev(c["ground_truth"]) for c in active}
    scored = {k: v for k, v in devs.items() if v is not None}
    worst_id = max(scored, key=scored.get)
    worst = scored[worst_id]

    assert worst_id == "A-30", (worst_id, worst)
    assert 0.1195 < worst < 0.12, worst          # запас до 12% — сотые доли п.п.
    assert worst <= ATWATER_TOL - 0.03, worst    # но до 15% запас настоящий
    assert check_atwater(active) == []


def test_check_c_links_catches_stale_expected_total():
    cases = _c_fixture()
    cases[0]["ground_truth"]["kcal"] = 150.0  # правка эталона A
    errs = check_c_links(cases)
    assert any("expected_total.kcal" in e for e in errs), errs


def test_check_c_links_tolerates_rounding_half():
    """Допуск ±0.05, а не побитово: эталон, попавший на «.x5», зависит от режима
    округления и не должен краснеть при смене реализации."""
    cases = _c_fixture()
    cases[2]["expected_total"]["fat"] = 11.95  # пересчёт даёт 12.0
    assert check_c_links(cases) == []
    cases[2]["expected_total"]["fat"] = 11.5   # а это уже настоящее расхождение
    assert check_c_links(cases) != []


@pytest.mark.parametrize(
    "mutate, expect_in",
    [
        (lambda cs: cs[0].update(status="missing_photo"), "в наборы не годится"),
        (lambda cs: cs[0]["ground_truth"].update(carbs=None), "не заполнен макрос carbs"),
        (lambda cs: cs[0]["ground_truth"].update(basis="per_portion"), "нужен per100g"),
        (lambda cs: cs[2]["ingredients"].append(
            {"case": "A-999", "g": 10, "text_name": "х"}), "несуществующий кейс"),
        (lambda cs: cs[2]["ingredients"][0].pop("text_name"), "нет text_name"),
        (lambda cs: cs[2]["ingredients"][0].update(text_name="  "), "нет text_name"),
    ],
)
def test_check_c_ingredients_catches(mutate, expect_in):
    cases = _c_fixture()
    mutate(cases)
    errs = check_c_ingredients(cases)
    assert any(expect_in in e for e in errs), errs


@pytest.mark.parametrize(
    "mutate, expect_in",
    [
        (lambda c: c.update(package_in_frame=True), "package_in_frame"),
        (lambda c: c["components"][0].pop("g"), "без взвешенной граммовки"),
        (lambda c: c["components"][0]["per100g"].pop("fat"), "без per100g"),
        (lambda c: c["components"][0].pop("ref_source"), "источник per100g не записан"),
    ],
)
def test_check_b_fields_catches(mutate, expect_in):
    case = _b_case()
    mutate(case)
    errs = check_b_fields([case])
    assert any(expect_in in e for e in errs), errs


def test_prod_key_alone_is_enough_for_eval(monkeypatch):
    """Прод-`.env` с ОДНИМ `LLM_API_KEY` обязан запускать замер (найдено на VPS).

    Второй ключ с тем же значением в `.env` — не конфигурация, а ловушка: он
    дублирует прод-секрет ради инструмента и рано или поздно разъезжается с ним.
    """
    from pydantic import SecretStr

    from app.config import settings

    prov = PROVIDERS[DEFAULT_PROVIDER]
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("sk-prod"))
    assert prov.key() == "sk-prod"


def test_dedicated_eval_key_wins_when_set(monkeypatch):
    """А заданный ОТДЕЛЬНО `OPENAI_API_KEY` задан намеренно (другая квота под
    замер) — молча игнорировать его нельзя."""
    from pydantic import SecretStr

    from app.config import settings

    prov = PROVIDERS[DEFAULT_PROVIDER]
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-eval"))
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("sk-prod"))
    assert prov.key() == "sk-eval"


def test_missing_key_message_lists_every_accepted_name(monkeypatch):
    """Сообщение об ошибке обязано назвать ВСЕ принимаемые имена.

    Прежнее «не задан settings.openai_api_key» было формально верным и
    практически ложным: прод-ключ уже лежал в `LLM_API_KEY`.
    """
    from app.config import settings

    prov = PROVIDERS[DEFAULT_PROVIDER]
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "llm_api_key", None)

    assert prov.key() is None
    assert "LLM_API_KEY" in prov.key_hint and "OPENAI_API_KEY" in prov.key_hint


def test_report_skips_foreign_json_in_runs_dir(tmp_path, monkeypatch, capsys):
    """`report` падал `KeyError: 'records'` на артефактах замеров OFF.

    В `runs/` лежат не только прогоны раннера: `off-rerank.json` и
    `off-veto.json` сохраняются туда же и имеют другую форму. Один посторонний
    файл делал недоступной сводку по ВСЕМ прогонам — то есть блокировал приёмку.
    Пропуск обязан быть ЯВНЫМ: прогон, у которого пропали записи, — испорченный
    прогон, и молчание о нём скрыло бы выпадение модели из сводки.
    """
    import json as _json

    from tests.eval import run_eval

    (tmp_path / "2026-08-01_off-veto.json").write_text(
        _json.dumps({"cases": [], "note": "артефакт замера источника"}),
        encoding="utf-8",
    )
    # Файл, на котором ПЕРВАЯ версия этой проверки всё равно упала: у него
    # `records` ЕСТЬ, а `provider` нет. Так пишет `check_off_value.py`
    # (`RUNS_DIR / "*_memory-tierA.json"`). Проверка по одному ключу была
    # проверкой по симптому, а не по форме.
    (tmp_path / "2026-08-03_memory-tierA.json").write_text(
        _json.dumps({"mode": "dish_text", "records": [{"id": "A-01"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path)

    with pytest.raises(SystemExit):   # валидных прогонов нет — но и не KeyError
        run_eval.build_report()
    out = capsys.readouterr().out
    assert out.count("пропущен (не прогон раннера") == 2


def test_runs_dir_holds_only_runner_artifacts():
    """Хвост №4: в `runs/` не должно быть ничего, кроме прогонов раннера.

    Тест на РЕАЛЬНОМ каталоге, а не на `tmp_path`: проверка пропуска чужих
    файлов (выше) гарантирует, что сводка не упадёт, но она же и позволяет чужим
    файлам копиться незамеченными. Разница существенная — падение чинили дважды,
    оба раза по симптому конкретного файла, потому что причина (общий каталог у
    двух инструментов с разными контрактами) не была видна ниоткуда.

    Замеры источника пишут в `tests/eval/probes/`.
    """
    import json as _json

    from tests.eval import run_eval

    strays = []
    for path in sorted(run_eval.RUNS_DIR.glob("*.json")):
        data = _json.loads(path.read_text(encoding="utf-8"))
        missing = set(run_eval._RUN_REQUIRED_KEYS) - set(data)
        if missing:
            strays.append(f"{path.name} (нет {sorted(missing)})")
    assert not strays, (
        "в каталоге прогонов раннера лежат чужие файлы: " + "; ".join(strays)
        + " — артефактам замеров место в tests/eval/probes/"
    )


def test_probe_scripts_write_outside_the_runs_dir():
    """Тот же хвост, но со стороны инструмента, а не результата.

    Проверка выше зелена, пока никто не запускал замер: она смотрит на файлы. Эта
    смотрит на КОД — куда замер положит результат в следующий раз. Без неё
    достаточно одного прогона `check_off_*.py` со старым дефолтом, чтобы каталог
    снова смешался, и мы бы узнали об этом падением сводки, как оба прошлых раза.
    """
    import re

    scripts = sorted((ROOT / "scripts").glob("check_off_*.py"))
    assert scripts, "не нашли ни одного скрипта замера — тест протух"

    out_names = re.compile(
        r"^\s*(?:DEFAULT_)?(?:OUT|CACHE|MEMORY)\s*=\s*(\w+)\s*/|"
        r'"--out",\s*default=str\((\w+)\s*/'
    )
    bad = []
    for path in scripts:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = out_names.match(line) or out_names.search(line)
            if m and (m.group(1) or m.group(2)) == "RUNS_DIR":
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, "замер пишет в каталог раннера: " + "; ".join(bad)


def test_run_filename_always_carries_the_prompt_version(tmp_path, monkeypatch):
    """Хвост №5: тег версии промпта в имени файла — ВСЕГДА, включая дефолт.

    Раньше тег ставился только для не-дефолтной версии, поэтому
    `2026-08-03_gpt-5.4-mini.json` не сообщал, что снят на v3, и через месяц был
    неотличим от прогона на v2. Имя файла — запись о прошлом, и зависеть от
    текущего значения переменной (`DEFAULT_PROMPT_VERSION`) оно не имеет права.
    """
    from app.contexts.nutrition.resolve import prompts
    from tests.eval import run_eval

    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path)
    out = run_eval._write_run(
        "openai", "gpt-5.4-mini", "A", 1, "",
        prompts.DEFAULT_PROMPT_VERSION, records=[], alerts=[],
    )

    assert f"_p-{prompts.DEFAULT_PROMPT_VERSION}" in out.name
    # И то же самое внутри JSON: имя файла удобно, но первоисточник — поле.
    import json as _json
    assert _json.loads(out.read_text(encoding="utf-8"))["prompt_version"] == (
        prompts.DEFAULT_PROMPT_VERSION
    )


def test_report_required_keys_cover_every_direct_read():
    """Структурный пин: каждый `d["..."]` в build_report обязан быть в списке.

    Сводка падала на постороннем файле ДВАЖДЫ — сперва на `records`, потом на
    `provider`, — потому что оба раза чинился конкретный ключ. Этот тест ловит
    следующий такой ключ в момент его появления, а не на прод-запуске у С.
    """
    import inspect
    import re

    from tests.eval import run_eval

    src = inspect.getsource(run_eval.build_report)
    # Обе формы прямого чтения: d["key"] и d['key'] внутри f-строк.
    read = set(re.findall(r"""d\[["']([a-z_]+)["']\]""", src))
    assert read, "не нашли ни одного прямого чтения — регулярка протухла"
    assert read <= set(run_eval._RUN_REQUIRED_KEYS), (
        f"читаются без проверки: {sorted(read - set(run_eval._RUN_REQUIRED_KEYS))}"
    )
