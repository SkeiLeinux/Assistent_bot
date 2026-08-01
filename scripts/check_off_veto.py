#!/usr/bin/env python3
"""check_off_veto.py — замер механизма «топ-1 + LLM-вето» (v5).

ЧТО ЭТО. Пятый по счёту механизм выбора карточки OFF, проверяемый на ОДНИХ И ТЕХ ЖЕ
25 кейсах яруса A. Предыдущие четыре — топ-1 по `_score`, `select_best`,
LLM-переранжирование (4 режима) — измерены в `rerank-accuracy-2026-08-01.md`.

ПРАВИЛО РЕШЕНИЯ (зафиксировано в отчёте ДО прогона, `veto-accuracy-2026-08-02.md` §0):
  · порог прежний и не смягчается: ≥80% верных при НУЛЕ уверенных ошибок,
    знаменатель — кейсы, где верная карточка есть в выдаче;
  · правило верности прежнее — ДВЕ независимые подписи (импортируются из
    check_off_coverage дословно, не копируются);
  · это ПОСЛЕДНЯЯ попытка на этом наборе. Не берёт порог → вариант D (только
    barcode). Шестого механизма на этих же 25 точках не будет.

ПОЧЕМУ ОСНОВНОЙ РЕЖИМ — БЕЗ ЧИСЕЛ КБЖУ В КАРТОЧКЕ. Метрика судит верность в том
числе по близости ккал. Если показать модели ккал карточки, она может отвечать
«да» по близости чисел — то есть по той самой величине, которой её потом
проверяют, и метрика начнёт мерить саму себя. Поэтому:
  · без чисел  → ОСНОВНОЙ счёт;
  · с числами  → дополнительный, ЧАСТИЧНО ЦИРКУЛЯРНЫЙ; расхождение между ними
    и есть измеренная величина циркулярности.

ИСТОЧНИК ДАННЫХ. Выдача OFF берётся из кэша прошлого замера
(`tests/eval/runs/2026-08-01_off-rerank.json`): те же запросы, те же карточки,
тот же порядок `_score`. Это не экономия сети, а условие сравнимости — пять
механизмов обязаны сравниваться на одной выдаче, а не в пяти разных прогонах.
Адаптер OFF не трогается: транспорт живёт в check_off_coverage.py (v4).

НЕЗАВИСИМЫЕ ПРОВЕРКИ (wave-plan §5; двух подписей мало — они часть правила
верности, а не проверка правила):
  (П1) ПОДСТАВНАЯ КАРТОЧКА — тот же вопрос, но карточка взята из выдачи ЧУЖОГО
       кейса (сдвиг на 7, взаимно простой с 25). Вето обязано ответить «нет».
       Массовое «да» означало бы, что «да» на реальном топ-1 не несёт информации.
       Выбор подставы не зависит ни от эталона, ни от подписей — только от номера.
  (П2) ИНВЕРТИРОВАННЫЙ ВОПРОС — «это ДРУГОЙ продукт?» на тех же парах. Если
       модель отвечает «да» и на «тот же?», и на «другой?», её ответ — уступчивость,
       а не суждение. Эталон в этой проверке не участвует вовсе.

Запуск:  python scripts/check_off_veto.py [--no-llm] [--cache FILE]
Сеть:    только api.openai.com (POST). В OFF не ходит — выдача из кэша.
Ничего не пишет в БД и не меняет прод.
"""

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Правило верности, маппинг карточек и транспорт к модели импортируются, а не
# копируются: ручная копия эталонов/правил внутри инструмента — ровно тот
# механизм, которым родился рассинхрон фото↔эталон на 11 кейсах.
from check_off_coverage import (  # noqa: E402
    KCAL_TOL, NAME_TOL, RERANK_N, RUNS_DIR,
    _best_name, _first_brand, attach_name_ok, classify, gate, macros_of,
    name_coverage, openai_chat, read_env_key, select_best, upper_bound,
)

DEFAULT_CACHE = RUNS_DIR / "2026-08-01_off-rerank.json"
DEFAULT_RUN = RUNS_DIR / "2026-08-01_gpt-5.4-mini.json"
DEFAULT_OUT = RUNS_DIR / "2026-08-02_off-veto.json"

BATCH = 5          # прод-форма: один вызов на разбор
PLACEBO_SHIFT = 7  # взаимно прост с 25 → перестановка без неподвижных точек

# ── Промпт вето. ОДНА формулировка, пред-зарегистрирована до прогона ─────────
# Критерии тождества продукта дословно те же, что в RERANK_SYSTEM: механизмы
# обязаны отличаться механизмом, а не описанием задачи. Поощрение отказа
# сохранено — в прошлом замере именно оно давало минимум уверенных ошибок.
# Второй формулировки не будет: подбор промпта по тем же 25 кейсам, на которых
# считается метрика, — подгонка под набор, а не замер.
VETO_SYSTEM = """\
Ты сверяешь позиции из дневника питания с карточками товаров из базы Open Food Facts.
На каждую позицию дана РОВНО ОДНА карточка. Вопрос один: карточка описывает ТОТ ЖЕ
продукт, что позиция, — да или нет?

Отвечай «нет», если это другой продукт или если уверенности нет: чужие числа КБЖУ
хуже, чем их отсутствие. «Нет» — нормальный и ожидаемый ответ.

Тем же продуктом НЕ являются:
- «сыр плавленый» и «сыр творожный»;
- «молоко 3.2%» и «молоко 1.5%»;
- «тунец в масле» и «тунец в собственном соку»;
- сухой продукт и он же в готовом (отварном) виде.
Если у позиции указан бренд и у карточки указан бренд — они должны совпадать.

Верни СТРОГО JSON-объект:
{"answers":[{"position":<номер позиции>,"same":true|false}]}
- ровно одна запись на каждую позицию;
- никакого текста вне JSON.\
"""

# Контроль П2: тот же вопрос наизнанку. Не режим механизма — проверка того, что
# ответ вето является суждением, а не уступчивостью («да» на любой вопрос).
VETO_SYSTEM_INVERTED = """\
Ты сверяешь позиции из дневника питания с карточками товаров из базы Open Food Facts.
На каждую позицию дана РОВНО ОДНА карточка. Вопрос один: карточка описывает ДРУГОЙ
продукт, не тот, что в позиции, — да или нет?

Отвечай «да» (другой продукт), если это другой продукт или если уверенности нет:
чужие числа КБЖУ хуже, чем их отсутствие.

Тем же продуктом НЕ являются:
- «сыр плавленый» и «сыр творожный»;
- «молоко 3.2%» и «молоко 1.5%»;
- «тунец в масле» и «тунец в собственном соку»;
- сухой продукт и он же в готовом (отварном) виде.
Если у позиции указан бренд и у карточки указан бренд — они должны совпадать.

Верни СТРОГО JSON-объект:
{"answers":[{"position":<номер позиции>,"different":true|false}]}
- ровно одна запись на каждую позицию;
- никакого текста вне JSON.\
"""


# ── Рендер запроса ──────────────────────────────────────────────────────────
def render_pair_batch(pairs, with_numbers):
    """pairs = [(row, card)]. Нумерация позиций с единицы."""
    out = []
    for pi, (row, card) in enumerate(pairs, 1):
        head = f"Позиция {pi}: {row['name']}"
        if row.get("brand"):
            head += f" | бренд: {row['brand']}"
        if row.get("fat_pct") is not None:
            head += f" | жирность: {row['fat_pct']:g}%"
        out.append(head)
        line = f"Карточка: {_best_name(card)}"
        brand = _first_brand(card.get("brands"))
        if brand:
            line += f" | бренд: {brand}"
        if with_numbers:
            m = macros_of(card)
            line += (f" | {m['kcal']:g} ккал, Б {m['protein']:g} "
                     f"Ж {m['fat']:g} У {m['carbs']:g}")
        out.append(line)
        out.append("")
    return "\n".join(out)


def parse_answers(text, n_positions, key, invert=False):
    """{"answers":[{"position":1,"same":true}]} → {позиция: bool}."""
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    except Exception:                            # noqa: BLE001 — кривой ответ = «нет»
        return {}
    out = {}
    for a in data.get("answers") or []:
        try:
            pos = int(a["position"])
        except (KeyError, TypeError, ValueError):
            continue
        v = a.get(key)
        if isinstance(v, str):
            v = v.strip().lower() in ("true", "да", "yes", "1")
        val = bool(v)
        out[pos] = (not val) if invert else val
    return {p: v for p, v in out.items() if 1 <= p <= n_positions}


def ask(pairs, api_key, label, with_numbers, system=VETO_SYSTEM,
        key="same", invert=False):
    """Батчами по BATCH. Возвращает {case_id: bool} + телеметрию вызовов."""
    answers, calls = {}, []
    for start in range(0, len(pairs), BATCH):
        batch = pairs[start:start + BATCH]
        user = render_pair_batch(batch, with_numbers)
        text, dt, usage, err = openai_chat(api_key, system, user, max_tokens=800)
        if err:
            print(f"    [{label}] батч {start // BATCH + 1}: ОШИБКА {err}")
            for row, _ in batch:
                answers[row["id"]] = False       # сбой вызова = отказ ступени
            calls.append({"error": err, "latency_s": dt})
            continue
        got = parse_answers(text, len(batch), key, invert)
        for pi, (row, _) in enumerate(batch, 1):
            answers[row["id"]] = bool(got.get(pi, False))
        calls.append({"error": None, "latency_s": dt, "usage": usage,
                      "n_positions": len(batch), "raw": text})
        print(f"    [{label}] батч {start // BATCH + 1}: {len(batch)} позиц. "
              f"за {dt:.2f} с, «да»: {sum(1 for p in range(1, len(batch) + 1) if got.get(p))}")
    return answers, calls


# ── Сводка ──────────────────────────────────────────────────────────────────
def line_gate(res, tag):
    right, den2, den1, errs, disp = gate(res)
    acc2 = f"{right / den2:.0%}" if den2 else "—"
    ok = bool(den2) and den2 and right / den2 >= 0.80 and errs == 0
    print(f"\n  ── {tag} " + "─" * max(0, 66 - len(tag)))
    print(f"    ВЕРНО {right} · СПОРНО {disp} · УВЕРЕННАЯ ОШИБКА {errs} · "
          f"ОТКАЗ {sum(1 for r in res if r['verdict'] == 'ОТКАЗ')}")
    print(f"    знаменатель (2) верная карточка в топ-{RERANK_N}: "
          f"{right}/{den2} = {acc2}   ← порог ADR-0019 §6 ≥80% при 0 ошибок")
    print(f"    знаменатель (1) все кейсы прогона          : "
          f"{right}/{den1} = {right / den1:.0%}")
    ub_r, ub_d = upper_bound(res)
    print(f"    ПОТОЛОК (всё «спорно» засчитано верным)    : {ub_r}/{ub_d} = "
          + (f"{ub_r / ub_d:.0%}" if ub_d else "—"))
    why = [] if ok else (
        ([f"точность {acc2} < 80%"] if not (den2 and right / den2 >= 0.80) else [])
        + ([f"уверенных ошибок {errs} ≠ 0"] if errs else []))
    print(f"    ГЕЙТ: {'ВЗЯТ' if ok else 'НЕ ВЗЯТ — ' + '; '.join(why)}")
    return {"right": right, "den2": den2, "den1": den1, "errors": errs,
            "disputed": disp, "gate": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--run", default=str(DEFAULT_RUN))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--replay", help="пересобрать отчёт из сохранённых ответов")
    args = ap.parse_args()

    for name, text in (("вето", VETO_SYSTEM), ("вето-инверт (контроль П2)",
                                               VETO_SYSTEM_INVERTED)):
        print(f"промпт {name} sha256: {hashlib.sha256(text.encode()).hexdigest()}")
    print(f"правило верности: ккал ≤{KCAL_TOL:.0%}, покрытие имени ≥{NAME_TOL} "
          f"(импортировано из check_off_coverage)")
    print()

    cached = json.loads(pathlib.Path(args.cache).read_text(encoding="utf-8"))
    rows = cached["rows"]
    attach_name_ok(rows, args.run)
    live = [r for r in rows if r.get("candidates")]
    print(f"выдача из кэша {args.cache}: {len(rows)} кейсов, "
          f"с кандидатами {len(live)}, топ-{RERANK_N} в порядке _score")

    store = {}
    if args.replay:
        store = json.loads(pathlib.Path(args.replay).read_text(encoding="utf-8"))["passes"]
    elif not args.no_llm:
        api_key = read_env_key()
        if not api_key:
            print("нет LLM_API_KEY — прогон невозможен")
            return 1
        print("\n" + "=" * 78)
        print("ПРОГОН — вето (батч по 5 = один вызов на разбор)")
        print("=" * 78)

        for tag, with_numbers in (("без чисел", False), ("с числами", True)):
            pairs1 = [(r, r["candidates"][0]) for r in live]
            a1, c1 = ask(pairs1, api_key, f"топ-1 {tag}", with_numbers)
            # Каскад: второй вопрос только там, где по топ-1 сказано «нет».
            pairs2 = [(r, r["candidates"][1]) for r in live
                      if not a1.get(r["id"]) and len(r["candidates"]) > 1]
            a2, c2 = ask(pairs2, api_key, f"топ-2 {tag}", with_numbers) if pairs2 else ({}, [])
            store[tag] = {"top1": a1, "top2": a2, "calls": c1 + c2,
                          "calls_top1": c1, "calls_top2": c2}

        # П1 — подставная карточка из выдачи чужого кейса.
        placebo_pairs, placebo_src = [], {}
        for i, r in enumerate(live):
            donor = live[(i + PLACEBO_SHIFT) % len(live)]
            placebo_pairs.append((r, donor["candidates"][0]))
            placebo_src[r["id"]] = donor["id"]
        ap1, cp = ask(placebo_pairs, api_key, "П1 подстава", False)
        store["П1 подстава"] = {"top1": ap1, "top2": {}, "calls": cp,
                                "donor": placebo_src}

        # П2 — тот же вопрос наизнанку на тех же парах топ-1.
        pairs_inv = [(r, r["candidates"][0]) for r in live]
        ai, ci = ask(pairs_inv, api_key, "П2 инверсия", False,
                     system=VETO_SYSTEM_INVERTED, key="different", invert=True)
        store["П2 инверсия"] = {"top1": ai, "top2": {}, "calls": ci}

        pathlib.Path(args.out).write_text(
            json.dumps({"passes": store, "cache": str(args.cache),
                        "prompt_sha": hashlib.sha256(VETO_SYSTEM.encode()).hexdigest()},
                       ensure_ascii=False), encoding="utf-8")
        print(f"\nответы сохранены: {args.out}")

    if not store:
        return 0

    # ── Механизмы из ответов ────────────────────────────────────────────────
    def picks_m1(tag):
        a1 = store[tag]["top1"]
        return {r["id"]: (0 if a1.get(r["id"]) else None) for r in rows}

    def picks_m2(tag):
        a1, a2 = store[tag]["top1"], store[tag]["top2"]
        out = {}
        for r in rows:
            if a1.get(r["id"]):
                out[r["id"]] = 0
            elif a2.get(r["id"]) and len(r.get("candidates") or []) > 1:
                out[r["id"]] = 1
            else:
                out[r["id"]] = None
        return out

    modes = {}
    for tag in ("без чисел", "с числами"):
        if tag in store:
            modes[f"M1 вето топ-1 ({tag})"] = picks_m1(tag)
            modes[f"M2 каскад топ-1→топ-2 ({tag})"] = picks_m2(tag)

    print("\n" + "=" * 78)
    print("ИТОГИ — ОСНОВНОЙ СЧЁТ (без чисел) и ДОПОЛНИТЕЛЬНЫЙ (с числами,")
    print("       частично циркулярный: подпись-ккал делит информацию с промптом)")
    print("=" * 78)
    probe = classify(rows, {})
    print(f"  кейсов: {len(rows)} · верная карточка (2 подписи) в топ-{RERANK_N}: "
          f"{sum(1 for r in probe if r['present'])}/{len(rows)}  ← знаменатель гейта")

    summary = {}
    for label, picks in modes.items():
        res = classify(rows, picks)
        summary[label] = line_gate(res, label)

    # ── Сравнение с ранее измеренными механизмами ───────────────────────────
    print("\n  ── СРАВНЕНИЕ ПО УВЕРЕННЫМ ОШИБКАМ (все механизмы, одна выдача) ──")
    prior = {
        "топ-1 по _score (без вызова)": {r["id"]: (0 if r.get("candidates") else None)
                                         for r in rows},
        "select_best (нынешний прод)": {
            r["id"]: select_best(r.get("candidates") or [], r["brand"], r["name"],
                                 r["fat_pct"]) for r in rows},
        "всегда последний кандидат": {
            r["id"]: (len(r["candidates"]) - 1 if r.get("candidates") else None)
            for r in rows},
    }
    rr = cached.get("rerank") or {}
    for mode, payload in rr.items():
        prior[f"LLM-переранжирование [{mode}]"] = payload["picks"]
    rowsfmt = "    {:<40} ВЕРНО {:>2} · СПОРНО {:>2} · ОШИБКА {:>2} · {}"
    for label, picks in list(prior.items()) + list(modes.items()):
        res = classify(rows, picks)
        right, den2, den1, errs, disp = gate(res)
        acc = f"{right}/{den2} = {right / den2:.0%}" if den2 else "—"
        print(rowsfmt.format(label[:40], right, disp, errs, acc))

    # ── Чувствительность ────────────────────────────────────────────────────
    print("\n  ── ЧУВСТВИТЕЛЬНОСТЬ (тот же прогон, другие правила верности) ──")
    variants = [
        ("базовое: ккал≤10%, покрытие≥0.5", {}),
        ("ккал ≤5%", {"kcal_tol": 0.05}),
        ("ккал ≤15%", {"kcal_tol": 0.15}),
        ("покрытие ≥0.34", {"name_tol": 0.34}),
        ("имя: главное слово", {"name_rule": "head"}),
        ("имя: покрытие ИЛИ главное слово", {"name_rule": "either"}),
    ]
    print(f"  {'правило':<34}", " ".join(f"{m[:22]:>24}" for m in modes))
    for label, kw in variants:
        cells = []
        for _, picks in modes.items():
            res = classify(rows, picks, **kw)
            right, den2, den1, errs, _ = gate(res)
            acc = f"{right}/{den2}" + (f"={right / den2:.0%}" if den2 else "")
            cells.append(f"{acc}{'!' if errs else ''}")
        print(f"  {label:<34}", " ".join(f"{c:>24}" for c in cells))
    print("  «!» — есть уверенные ошибки, гейт не взят независимо от точности.")

    # ── Независимые проверки ────────────────────────────────────────────────
    print("\n  ── П1. ПОДСТАВНАЯ КАРТОЧКА (карточка из выдачи чужого кейса) ──")
    print("  Эталон в этой проверке не участвует. Вето обязано отвечать «нет».")
    if "П1 подстава" in store:
        ap1 = store["П1 подстава"]["top1"]
        donor = store["П1 подстава"].get("donor", {})
        yes = [r for r in live if ap1.get(r["id"])]
        print(f"    «да» на подставу: {len(yes)}/{len(live)} = {len(yes) / len(live):.0%}")
        for r in yes:
            d = next((x for x in live if x["id"] == donor.get(r["id"])), None)
            card = d["candidates"][0] if d else None
            print(f"      {r['id']}: «{r['name'][:34]}» ← подстава из {donor.get(r['id'])}: "
                  f"«{_best_name(card)[:34] if card else '—'}»")
        base_yes = sum(1 for r in live if store["без чисел"]["top1"].get(r["id"]))
        print(f"    для сравнения «да» на настоящий топ-1 (без чисел): "
              f"{base_yes}/{len(live)} = {base_yes / len(live):.0%}")

    print("\n  ── П2. ИНВЕРТИРОВАННЫЙ ВОПРОС («это ДРУГОЙ продукт?») ──")
    print("  Проверяет, что ответ — суждение, а не уступчивость. Эталон не участвует.")
    if "П2 инверсия" in store:
        ai = store["П2 инверсия"]["top1"]
        a1 = store["без чисел"]["top1"]
        agree = [r for r in live if bool(ai.get(r["id"])) == bool(a1.get(r["id"]))]
        print(f"    согласие прямого и инвертированного вопроса: "
              f"{len(agree)}/{len(live)} = {len(agree) / len(live):.0%}")
        for r in live:
            if bool(ai.get(r["id"])) != bool(a1.get(r["id"])):
                print(f"      расхождение {r['id']}: прямой "
                      f"{'да' if a1.get(r['id']) else 'нет'}, инверт. "
                      f"{'да' if ai.get(r['id']) else 'нет'} — «{r['name'][:38]}»")

    # ── Стоимость и латентность ─────────────────────────────────────────────
    print("\n  ── СТОИМОСТЬ И ЛАТЕНТНОСТЬ ──")
    for tag in ("без чисел", "с числами"):
        if tag not in store:
            continue
        calls = [c for c in store[tag]["calls"] if not c.get("error")]
        if not calls:
            continue
        lat = sorted(c["latency_s"] for c in calls)
        tin = sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in calls)
        tout = sum((c.get("usage") or {}).get("completion_tokens", 0) for c in calls)
        n1 = len(store[tag].get("calls_top1") or [])
        n2 = len(store[tag].get("calls_top2") or [])
        print(f"    [{tag}] вызовов {len(calls)} (топ-1 {n1} + топ-2 {n2}), "
              f"латентность медиана {lat[len(lat) // 2]:.2f} с, максимум {max(lat):.2f} с")
        print(f"           токенов на вызов: вход {tin / len(calls):.0f}, "
              f"выход {tout / len(calls):.0f}; на позицию: вход "
              f"{tin / max(1, sum(c.get('n_positions', 0) for c in calls)):.0f}")

    # ── Покейсово ───────────────────────────────────────────────────────────
    print("\n  ── ПОКЕЙСОВО, ОСНОВНОЙ РЕЖИМ (для ручной перепроверки) ──")
    for label in [k for k in modes if "без чисел" in k]:
        res = classify(rows, modes[label])
        by_id = {r["id"]: r for r in res}
        print(f"\n  [{label}]")
        for row in rows:
            r = by_id[row["id"]]
            ref, card = row["ref"], r["card"]
            got = (f"«{_best_name(card)[:34]}» {macros_of(card)['kcal']:g} ккал"
                   if card else "— (вето: нет)")
            cov = f"{name_coverage(card, ref['name'], ref['brand']):.2f}" if card else "—"
            print(f"    {row['id']:6} {r['verdict']:<7} эталон {ref['kcal']:>6.1f} "
                  f"«{ref['name'][:30]}»")
            print(f"           взято: {got}  покрытие {cov}  "
                  f"{'верная В ВЫДАЧЕ' if r['present'] else 'верной в выдаче НЕТ'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
