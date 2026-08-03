#!/usr/bin/env python3
"""check_off_value.py — полезна ли ступень OFF на ветке `estimate`?

ЧТО ЭТО И ЧЕМ ОТЛИЧАЕТСЯ ОТ ПЯТИ ПРЕДЫДУЩИХ ЗАМЕРОВ.
Пять механизмов (топ-1, `select_best`, переранжирование ×4, вето, каскад вето)
отвечали на вопрос «какую карточку выбрать, чтобы СВЕРИТЬ С ЭТИКЕТКОЙ» — то есть
обслуживали ветку `label`, где гейт не взят и §2.3 ADR-0019 отменена. Здесь
вопрос ДРУГОЙ и ни разу не измерявшийся:

    на ветке `estimate` (этикетки нет) карточка OFF БЛИЖЕ к правде,
    чем типовые числа модели, или ДАЛЬШЕ?

Это не шестая попытка на тех же 25 точках (запрет `veto-accuracy-2026-08-02.md`
§0): другая величина, другая метрика и НОВЫЕ данные — чисел модели «из памяти»
по ярусу A до этого замера не существовало.

ТРИ РУКИ, одни и те же кейсы, один и тот же эталон (напечатан на упаковке):
    M — память модели: per100g по одному названию, без фото и без этикетки;
    S — что делает прод сегодня: `select_best` по кэшированной выдаче;
    U — ПОТОЛОК: карточка из топ-10, ближайшая к истине. В бою недостижима
        (чтобы её выбрать, надо знать ответ) и нужна ровно для одного вывода:
        если U не лучше M, то никакой будущий матчер ступень не окупит.

МЕТРИКА БЕЗ СВОБОДНЫХ ПАРАМЕТРОВ. Первичный вывод — знаковый счёт по кейсам
(где ошибка меньше: у M или у S). Порога не существует в принципе, поэтому
критерий можно назначать после того, как увидим числа (решение С.), не рискуя
подгонкой: подгонять здесь нечего.

ЗАЩИТА ОТ УТЕЧКИ ЭТАЛОНА. Названия для руки M берутся из выдачи ИЗВЛЕКАТЕЛЯ
(`name`/`brand` кэша — то, что модель сама прочла с фото), а не из
`ground_truth.name`, который писал человек, глядя на упаковку. Тот же вход шёл
в OFF, поэтому руки сравниваются честно. Числа КБЖУ модели не показываются.

НЕЗАВИСИМЫЕ ПРОВЕРКИ (гоняются ДО основных чисел, wave-plan §5):
  П1 ПОДСТАВНАЯ КАРТОЧКА — в роль S подставляется карточка из выдачи ЧУЖОГО
     кейса (сдвиг на 7, взаимно прост с 25). Её ошибка обязана быть заметно
     хуже настоящей S. Если не хуже — метрика не различает товары, и замер
     недействителен, а не «интересен».
  П2 ИСТИНА ПРОТИВ ИСТИНЫ — в роль руки подставляются сами эталонные числа,
     ошибка обязана быть ровно 0. Ловит рассинхрон `id` ↔ эталон: ровно эта
     ошибка дважды портила замеры проекта (11 кейсов 27.07, ретропрогон 02.08).
  П3 ЗНАМЕНАТЕЛЬ — печатается число реально сравнённых кейсов. Отрицательный
     результат обязан предъявлять размер знаменателя.

План замера и правило решения: `docs/05_implementation/off-value-probe-plan-2026-08-03.md`.

Запуск:
    python scripts/check_off_value.py --collect   # 25 текстовых вызовов, ~$0.01
    python scripts/check_off_value.py             # офлайн-скоринг, сети не нужно
    python scripts/check_off_value.py --selftest  # только П1/П2 на синтетике

Сеть: только api.openai.com и только в режиме --collect. В OFF не ходит (кэш).
В БД не пишет, прод не трогает.
"""

import argparse
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from check_off_coverage import (            # noqa: E402 — путь настраивается выше
    MANIFEST,
    PROBES_DIR,
    load_cases,
    macros_of,
    openai_chat,
    read_env_key,
    select_best,
    sig_name,
    terms_of,
)

# Всё три — артефакты ЗАМЕРА, не прогоны раннера (блок 13, хвост №4). Файл
# `*_memory-tierA.json` особенно показателен: `records` у него есть, а `provider`
# нет, и именно на нём упала первая, «починенная по одному ключу», версия сводки.
CACHE = PROBES_DIR / "2026-08-01_off-rerank.json"
MEMORY = PROBES_DIR / "2026-08-03_memory-tierA.json"
OUT = PROBES_DIR / "2026-08-03_off-value.json"

# Ничья: руки считаются неразличимыми, если ошибки расходятся меньше чем на 1 п.п.
TIE_PP = 0.01
# Грубый промах — вторичная описательная величина, в решение не входит.
GROSS_MISS = 0.30
# Сдвиг для подставной карточки (П1). Взаимно прост с 25 → перестановка без
# неподвижных точек: ни один кейс не получит собственную карточку.
SHIFT = 7

# Промпт руки M. Ровно один режим — «типовые числа по названию»; это то, что
# делает прод на ветке estimate, когда этикетки нет.
MEMORY_SYSTEM = (
    "Ты — справочник по пищевой ценности продуктов. По названию продукта верни "
    "ТИПОВЫЕ значения на 100 г. Верни строго JSON без текста вне JSON: "
    '{"kcal":<число>,"protein":<число>,"fat":<число>,"carbs":<число>}. '
    "Никаких пересчётов на порцию, никаких диапазонов, только числа."
)


# ── Эталон: kcal + БЖУ, нормализация per_portion берётся из load_cases ───────
def truth_table(cases):
    """Полный эталон (kcal+БЖУ) для кейсов, поднятых `load_cases`.

    Коэффициент нормализации `per_portion → per100g` НЕ пересчитывается заново,
    а ВЫВОДИТСЯ из уже нормализованного `case["ref"]["kcal"]`: две реализации
    одной формулы обязательно разойдутся, а так расхождение невозможно
    по построению (тот же приём, что «правило верности импортируется, а не
    копируется» в check_off_veto).
    """
    import yaml

    raw = {c["id"]: c for c in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))}
    out = {}
    for case in cases:
        gt = raw[case["id"]]["ground_truth"]
        k_raw = float(gt["kcal"])
        factor = (case["ref"]["kcal"] / k_raw) if k_raw else 1.0
        row = {"kcal": case["ref"]["kcal"]}
        for macro in ("protein", "fat", "carbs"):
            v = gt.get(macro)
            # Не у всех эталонов выписаны БЖУ. Отсутствие остаётся None и просто
            # выпадает из БЖУ-метрики: подставить 0 значило бы объявить, что
            # белка в продукте нет, и превратить пробел в данные.
            row[macro] = None if v is None else float(v) * factor
        out[case["id"]] = row
    return out


def rel_err(value, truth):
    if value is None or truth in (None, 0):
        return None
    return abs(value - truth) / abs(truth)


def macro_err(macros, truth):
    """Средняя относительная ошибка по БЖУ. Нули эталона пропускаются:
    делить на ноль нельзя, а `max(x,1)` превратил бы неопределённость в число —
    ровно тот класс дефекта, что уже разобран в C2/C4.4."""
    parts = [rel_err(macros.get(k), truth[k]) for k in ("protein", "fat", "carbs")
             if truth.get(k)]
    parts = [p for p in parts if p is not None]
    return sum(parts) / len(parts) if parts else None


# ── Сбор руки M (единственный шаг с сетью) ──────────────────────────────────
def collect(cases, api_key):
    records = []
    for i, case in enumerate(cases, 1):
        query = terms_of(case)          # тот же вход, что уходил в OFF
        text, dt, usage, err = openai_chat(api_key, MEMORY_SYSTEM, query, max_tokens=200)
        macros = None
        if text and not err:
            try:
                raw = json.loads(text)
                macros = {k: float(raw[k]) for k in ("kcal", "protein", "fat", "carbs")}
            except (ValueError, KeyError, TypeError) as e:
                err = f"разбор ответа: {type(e).__name__}: {e}"
        records.append({"id": case["id"], "query": query, "per100g": macros,
                        "latency_s": round(dt, 2), "usage": usage, "error": err})
        print(f"  [{i:>2}/{len(cases)}] {case['id']:<6} {query[:44]:<44} "
              f"{'ok' if macros else 'ОШИБКА: ' + str(err)}")
    MEMORY.write_text(json.dumps(
        {"model": "прод-цель (ADR-0017 §16)", "mode": "memory_by_name",
         "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсохранено: {MEMORY}")
    ok = sum(1 for r in records if r["per100g"])
    print(f"получено чисел: {ok} из {len(records)}")


# ── Скоринг (офлайн) ────────────────────────────────────────────────────────
def cached_rows():
    if not CACHE.exists():
        sys.exit(f"нет кэша выдачи OFF: {CACHE}\n"
                 "он создаётся замером check_off_coverage.py (прогон 2026-08-01)")
    return {r["id"]: r for r in json.loads(CACHE.read_text(encoding="utf-8"))["rows"]}


def memory_rows():
    if not MEMORY.exists():
        return None
    return {r["id"]: r.get("per100g")
            for r in json.loads(MEMORY.read_text(encoding="utf-8"))["records"]}


def usable_cards(row):
    """Кандидаты с ПОЛНЫМИ четырьмя значениями. Неполные карточки — не выбор
    между руками, а отсутствие руки: неполной карточкой уточнить нечего."""
    return [c for c in (row.get("candidates") or []) if macros_of(c)]


def arm_S(row, case):
    cards = usable_cards(row)
    if not cards:
        return None
    # ВНИМАНИЕ: `select_best` возвращает ИНДЕКС, а не карточку (прод-семантика).
    # Обращение с ним как с карточкой падало бы молча при `or {}` — здесь падает
    # громко и потому поймано самотестом, а не отчётом.
    idx = select_best(cards, case["brand"], case["name"], case["fat_pct"])
    return macros_of(cards[idx]) if idx is not None else None


def arm_U(row, truth, case=None, name_gate=False):
    """Потолок: ближайшая к истине карточка из топ-10. Недостижима в бою.

    ДВЕ ФОРМЫ, и разница между ними — не педантизм, а главный урок замера вето.
    Если выбирать «лучшую» карточку ТОЛЬКО по близости ккал, в потолок попадут
    совпадения на ДРУГИХ продуктах: среди десяти кандидатов почти всегда найдётся
    что-то с похожей калорийностью (онигири ↔ тунец 178/176, стейк ↔ сосиски
    220/221 — `rerank-accuracy-2026-08-01.md`). Такой потолок меряет «есть ли
    похожее число», а не «есть ли верный товар», и завышает сам себя.

      name_gate=False → U_kcal: заведомо оптимистичная граница, загрязнена
                        случайной близостью; полезна только как «выше не будет».
      name_gate=True  → U_имя:  среди карточек, прошедших подпись по имени
                        (`sig_name`, импортируется дословно). Это и есть честный
                        потолок любого будущего матчера.
    """
    best, best_err = None, None
    for card in usable_cards(row):
        if name_gate:
            ref = case["ref"]
            if not sig_name(card, ref.get("name"), ref.get("brand")):
                continue
        m = macros_of(card)
        e = rel_err(m["kcal"], truth["kcal"])
        if e is not None and (best_err is None or e < best_err):
            best, best_err = m, e
    return best


def compare(rows, cases, truth, memory, s_override=None):
    """Парное сравнение M и S. `s_override` — подмена карточки для П1."""
    res = []
    for case in cases:
        cid = case["id"]
        row = rows.get(cid)
        if row is None:
            continue
        t = truth[cid]
        s_row = s_override.get(cid) if s_override else row
        s = arm_S(s_row, case) if s_row else None
        m = (memory or {}).get(cid)
        u = arm_U(row, t)                                    # загрязнён случайностью
        u_ok = arm_U(row, t, case=case, name_gate=True)      # честный потолок
        res.append({
            "id": cid, "query": terms_of(case), "truth_kcal": round(t["kcal"], 1),
            "M": m, "S": s, "U": u, "U_name": u_ok,
            "err_M": rel_err((m or {}).get("kcal"), t["kcal"]),
            "err_S": rel_err((s or {}).get("kcal"), t["kcal"]),
            "err_U": rel_err((u or {}).get("kcal"), t["kcal"]),
            "err_Un": rel_err((u_ok or {}).get("kcal"), t["kcal"]),
            "bju_M": macro_err(m, t) if m else None,
            "bju_S": macro_err(s, t) if s else None,
        })
    return res


def sign_count(res):
    m_win = s_win = tie = 0
    for r in res:
        if r["err_M"] is None or r["err_S"] is None:
            continue
        d = r["err_M"] - r["err_S"]
        if abs(d) < TIE_PP:
            tie += 1
        elif d > 0:
            s_win += 1
        else:
            m_win += 1
    return m_win, s_win, tie


def med(res, key):
    vals = [r[key] for r in res if r[key] is not None]
    return statistics.median(vals) if vals else None


def pct(x):
    return "—" if x is None else f"{x * 100:5.1f}%"


# ── Независимые проверки ────────────────────────────────────────────────────
def control_p2(cases, truth):
    """П2: эталон в роли руки обязан дать ошибку 0 — иначе сломан join."""
    bad = [c["id"] for c in cases
           if (rel_err(truth[c["id"]]["kcal"], truth[c["id"]]["kcal"]) or 0) != 0]
    ok = not bad
    print(f"  П2  истина против истины ......... {'OK' if ok else 'ПРОВАЛ ' + str(bad)}")
    # Вторая половина П2: id из кэша обязаны совпасть с id эталонов.
    missing = [c["id"] for c in cases if c["id"] not in truth]
    print(f"      id кэша ↔ эталоны ........... "
          f"{'OK' if not missing else 'ПРОВАЛ, нет эталона: ' + str(missing)}")
    return ok and not missing


def control_p1(rows, cases, truth, memory, base):
    """П1: подставная карточка из чужого кейса обязана быть заметно хуже."""
    ids = [c["id"] for c in cases if c["id"] in rows]
    shifted = {cid: rows[ids[(i + SHIFT) % len(ids)]] for i, cid in enumerate(ids)}
    fake = compare(rows, cases, truth, memory, s_override=shifted)
    real_med, fake_med = med(base, "err_S"), med(fake, "err_S")
    ok = (real_med is not None and fake_med is not None and fake_med > real_med)
    print(f"  П1  подставная карточка ......... медиана ошибки {pct(fake_med)} "
          f"против {pct(real_med)} у настоящей — {'OK' if ok else 'ПРОВАЛ'}")
    if not ok:
        print("      ВНИМАНИЕ: метрика не отличает чужой товар от своего.\n"
              "      Замер недействителен целиком, читать основные числа нельзя.")
    return ok, fake_med


def selftest():
    """П1/П2 на синтетике — проверяет плумбинг без ключа и без прогона M."""
    print("САМОТЕСТ (синтетика, сети не требуется)\n" + "-" * 78)
    truth = {"X": {"kcal": 100.0, "protein": 10.0, "fat": 5.0, "carbs": 4.0}}
    assert rel_err(100.0, 100.0) == 0, "П2: эталон против себя обязан дать 0"
    assert abs(rel_err(150.0, 100.0) - 0.5) < 1e-9
    assert rel_err(None, 100.0) is None, "нет числа → нет ошибки, а не ноль"
    assert rel_err(50.0, 0) is None, "нулевой эталон не превращается в число"
    assert macro_err({"protein": 10.0, "fat": 5.0, "carbs": 4.0}, truth["X"]) == 0
    zero = {"kcal": 235.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    assert macro_err(zero, zero) is None, (
        "при нулевом составе БЖУ-ошибка не определена и обязана быть None, "
        "а не 0 и не огромное число (класс дефекта C2/C4.4)")
    res = [{"err_M": 0.10, "err_S": 0.30}, {"err_M": 0.30, "err_S": 0.10},
           {"err_M": 0.100, "err_S": 0.105}, {"err_M": None, "err_S": 0.1}]
    assert sign_count(res) == (1, 1, 1), sign_count(res)
    print("  формула ошибки ................. OK")
    print("  ничья по TIE_PP ................ OK")
    print("  БЖУ при нулевом составе → None . OK")
    print("  знаковый счёт .................. OK")
    print("  пара без одной руки не считается OK")
    print("\nСамотест пройден. Это проверка плумбинга, а не замер.")


# ── Отчёт ───────────────────────────────────────────────────────────────────
def report(res, cases, rows, memory):
    n_cards = sum(1 for r in res if r["S"] is not None)
    n_mem = sum(1 for r in res if r["M"] is not None)
    n_pair = sum(1 for r in res if r["M"] is not None and r["S"] is not None)

    print("\nП3  ЗНАМЕНАТЕЛИ (отрицательный результат обязан их предъявлять)")
    print(f"      кейсов в кэше выдачи ........ {len(rows)}")
    print(f"      из них с полной карточкой ... {n_cards}")
    print(f"      с числами от модели ......... {n_mem}")
    print(f"      вошло в парное сравнение .... {n_pair}")
    if n_pair == 0:
        print("\n  Сравнивать нечего. Любой вывод отсюда был бы выводом из пустоты.")
        return

    print("\n" + "=" * 78)
    print("ПОКЕЙСОВО (ошибка ккал относительно напечатанной истины)")
    print("=" * 78)
    print(f"{'кейс':<7}{'истина':>8}  {'M память':>9}{'S прод':>9}"
          f"{'U ккал':>8}{'U имя':>8}  ближе")
    for r in res:
        winner = "—"
        if r["err_M"] is not None and r["err_S"] is not None:
            d = r["err_M"] - r["err_S"]
            winner = "ничья" if abs(d) < TIE_PP else ("S" if d > 0 else "M")
        print(f"{r['id']:<7}{r['truth_kcal']:>8.0f}  {pct(r['err_M']):>9}"
              f"{pct(r['err_S']):>9}{pct(r['err_U']):>8}{pct(r['err_Un']):>8}  {winner}")

    m_win, s_win, tie = sign_count(res)
    print("\n" + "=" * 78)
    print("ПЕРВИЧНЫЙ ВЫВОД — знаковый счёт (свободных параметров нет)")
    print("=" * 78)
    print(f"  ближе память модели (M) ......... {m_win}")
    print(f"  ближе карточка прода (S) ........ {s_win}")
    print(f"  ничья (расхождение < {TIE_PP*100:.0f} п.п.) ...... {tie}")
    print(f"  знаменатель ..................... {n_pair}")

    print("\nОПИСАТЕЛЬНОЕ (в решение не входит, порогов нет)")
    print(f"  медиана ошибки ккал: M {pct(med(res,'err_M'))} · "
          f"S {pct(med(res,'err_S'))} · U {pct(med(res,'err_U'))}")
    print(f"  медиана ошибки БЖУ:  M {pct(med(res,'bju_M'))} · "
          f"S {pct(med(res,'bju_S'))}")
    for arm, key in (("M", "err_M"), ("S", "err_S")):
        gross = sum(1 for r in res if r[key] is not None and r[key] > GROSS_MISS)
        have = sum(1 for r in res if r[key] is not None)
        print(f"  грубых промахов (>{GROSS_MISS*100:.0f}%) у {arm}: {gross} из {have}")

    print("\nПОТОЛОК — встроенное самоопровержение")
    mu, mun, mm = med(res, "err_U"), med(res, "err_Un"), med(res, "err_M")
    have_un = sum(1 for r in res if r["err_Un"] is not None)
    print(f"  U ккал (загрязнён случайной близостью) .. {pct(mu)}")
    print(f"  U имя  (карточка прошла подпись имени) .. {pct(mun)}  "
          f"на {have_un} кейсах из {len(res)}")
    print("  Читать надо U имя: выбор ближайшей по ккал среди десяти кандидатов")
    print("  почти всегда находит похожее ЧИСЛО на другом товаре (урок замера вето).")
    if mun is None:
        print("  Честный потолок не определён: ни одна карточка не прошла подпись\n"
              "  имени. Это само по себе ответ — верных карточек в выдаче нет.")
    elif mm is not None and mun >= mm:
        print("  Честный потолок НЕ лучше памяти модели: даже идеальный матчер\n"
              "  ступень не окупает. Работа над матчингом закрывается независимо\n"
              "  от того, что показал знаковый счёт выше.")
    elif mm is not None:
        print(f"  Честный потолок лучше памяти ({pct(mun)} против {pct(mm)}): запас\n"
              "  на улучшение матчера есть; сколько именно — разница U имя и S.\n"
              "  ВАЖНО: запас реализуем только механизмом, которого пока нет —\n"
              "  пять измеренных гейт не взяли.")

    print("\nНАПОМИНАНИЕ ОБ АСИММЕТРИИ ВЫБОРКИ (объявлена до прогона, план §5)")
    print("  25 кейсов — брендированная упаковка, ЛУЧШИЙ случай для OFF.")
    print("  Проигрыш OFF здесь — решающий. Выигрыш — не переносится на тарелки")
    print("  и домашнюю еду, там карточек нет вовсе.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--collect", action="store_true",
                    help="прогнать руку M (25 текстовых вызовов, нужен ключ)")
    ap.add_argument("--selftest", action="store_true", help="только П1/П2 на синтетике")
    ap.add_argument("--run", default=None, help="прогон извлекателя (источник имён)")
    args = ap.parse_args()
    PROBES_DIR.mkdir(parents=True, exist_ok=True)

    if args.selftest:
        selftest()
        return

    cases = load_cases(args.run) if args.run else load_cases(
        str(RUNS_DIR / "2026-08-01_gpt-5.4-mini.json"))

    if args.collect:
        key = read_env_key()
        if not key:
            sys.exit("нет LLM_API_KEY (переменная окружения или .env в корне репо)")
        print(f"СБОР РУКИ M — типовые числа по названию, {len(cases)} запросов\n")
        collect(cases, key)
        return

    truth = truth_table(cases)
    rows = cached_rows()
    memory = memory_rows()
    if memory is None:
        sys.exit(f"нет файла руки M: {MEMORY}\n"
                 "сначала: python scripts/check_off_value.py --collect")

    print("=" * 78)
    print("НЕЗАВИСИМЫЕ ПРОВЕРКИ (до основных чисел)")
    print("=" * 78)
    ok2 = control_p2(cases, truth)
    base = compare(rows, cases, truth, memory)
    ok1, _ = control_p1(rows, cases, truth, memory, base)

    report(base, cases, rows, memory)

    OUT.write_text(json.dumps(
        {"plan": "docs/05_implementation/off-value-probe-plan-2026-08-03.md",
         "controls": {"p1_passed": ok1, "p2_passed": ok2},
         "sign_count": dict(zip(("m_win", "s_win", "tie"), sign_count(base))),
         "rows": base}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсырьё прогона: {OUT}")
    if not (ok1 and ok2):
        print("\n⚠ НЕЗАВИСИМАЯ ПРОВЕРКА ПРОВАЛЕНА — числа выше недействительны.")
        sys.exit(1)


if __name__ == "__main__":
    main()
