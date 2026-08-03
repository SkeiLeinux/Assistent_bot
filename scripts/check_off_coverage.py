#!/usr/bin/env python3
"""check_off_coverage.py — диагностика ступени OFF и замер матчинга (v4).

ИСТОРИЯ ЗАМЕРА (важна, чтобы не повторить ошибки):
  v1 — ходил в `/cgi/search.pl`: 18 из 25 запросов → HTTP 503 за 0.06 с. Причина не
       в лимитах: легаси-бэкенд поиска OFF (Perl), питающий и `/cgi/search.pl`, и
       `/api/v2/search`, устарел и отдаёт 503 массово. Замер недействителен.
  v2 — перешёл на search-a-licious: 200 OK, 25/25 «найдено». Тоже недействителен:
       скрипт брал первую карточку с полным КБЖУ, минуя `select_best()` из прода.
       Elasticsearch всегда возвращает топ-20 по релевантности, поэтому «найдено»
       означало «что-то вернулось», а не «нашёлся нужный товар».
  v3 — перенёс `select_best()` дословно: покрытие 80%, точность матчинга 36%,
       порог скора не помогает ни при каком значении. Действителен.
  v4 — ЭТОТ. Замеряет точность LLM-переранжирования (ADR-0019 §6 п.2, гейт ≥80%
       при нуле уверенных ошибок) на тех же кейсах и на ТОЙ ЖЕ выдаче, что и
       детерминированный `select_best` — то есть A и B сравниваются на одних
       данных, а не в двух разных прогонах.

ЧТО ИСПРАВЛЕНО ОТНОСИТЕЛЬНО v3 (обе ошибки найдены при подготовке v4):
  1. Таблица кейсов больше НЕ хардкодится в скрипте. Запросы берутся из
     сохранённого прогона прод-пути (`tests/eval/runs/*_gpt-5.4-mini.json`),
     эталоны — из `tests/eval/manifest.yaml`. Ручная копия эталонов внутри
     инструмента — ровно тот механизм, которым родился рассинхрон фото↔эталон
     на 11 кейсах (`llm-eval-dataset.md` §9).
  2. A-09 в v3 сравнивался с эталоном 695 ккал, а это `basis: per_portion`
     (500 г). Карточка OFF даёт КБЖУ на 100 г, поэтому кейс не мог «сойтись»
     ни при каком матчинге. v4 приводит эталон к 100 г по `portion_grams`.

── ПРАВИЛО ВЕРНОСТИ ВЫБОРА (зафиксировано ДО прогона) ───────────────────────
Близость ккал — ПРОКСИ, а не истина: разные продукты дают близкие ккал (детектор
рассинхрона уже ловил ложные сигналы на 339 против 342). Поэтому верность
подтверждается ДВУМЯ независимыми подписями:

  ПОДПИСЬ-ККАЛ   |kcal_карточки − kcal_эталона| / kcal_эталона ≤ KCAL_TOL
  ПОДПИСЬ-ИМЯ    доля основ слов эталонного названия (name+brand из manifest),
                 покрытых названием карточки, ≥ NAME_TOL
                 основа = первые 5 символов слова длиной ≥ 4

  обе сошлись → ВЕРНО   ровно одна → СПОРНО (не в числитель!)   ни одной → ОШИБКА
  УВЕРЕННАЯ ОШИБКА = модель ВЫБРАЛА карточку, неверную по ОБЕИМ подписям.

Подпись-имя обязана быть механической, а не «на глаз»: судить о верности выбора
глазами той же модели, которая выбирала, — значит проверять её ею же. Поэтому
правило зафиксировано формулой, а отчёт печатает по каждому кейсу и эталонное
имя, и имя выбранной карточки — чтобы каждую строку можно было перепроверить
руками. Плюс §ЧУВСТВИТЕЛЬНОСТЬ: вердикт гейта пересчитывается при других
порогах и другом правиле имени — если он от них зависит, замер не состоялся.

ДВА ЗНАМЕНАТЕЛЯ, оба печатаются раздельно:
  (1) все кейсы прогона        — сколько позиций вообще получат провенанс off;
  (2) кейсы, где верная карточка ЕСТЬ в топ-N — то, что меряет сам механизм
      выбора. Порог ADR-0019 §6 задан для (2); (1) показывается, чтобы «80%»
      нельзя было прочитать как «80% позиций разрешатся источником».

Запуск:  python scripts/check_off_coverage.py [--no-llm] [--cache FILE]
Сеть:    search.openfoodfacts.org (GET) + api.openai.com (POST, ключ из .env).
Ничего не пишет в БД и не меняет прод. Артефакт прогона — JSON в
`tests/eval/probes/` (НЕ в `runs/`: тот каталог принадлежит раннеру, и посторонний
файл в нём дважды ронял его сводку — блок 13, хвост №4).
"""

import argparse
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "eval" / "manifest.yaml"
# ЧИТАЕМ из runs/ — там прогоны раннера, они источник запросов для замера.
RUNS_DIR = ROOT / "tests" / "eval" / "runs"
DEFAULT_RUN = RUNS_DIR / "2026-08-01_gpt-5.4-mini.json"
# ПИШЕМ в probes/ — свой каталог. Замеры источника складывали артефакты в runs/,
# и `report` раннера дважды падал на них `KeyError`: у файлов другая форма, а один
# посторонний файл делал недоступной сводку по ВСЕМ прогонам, то есть блокировал
# приёмку. Каталог прогонов принадлежит раннеру; у замеров он свой.
PROBES_DIR = ROOT / "tests" / "eval" / "probes"

USER_AGENT = "lifeManager_bot/1.0 (skyline987650@gmail.com)"
REQUEST_TIMEOUT_S = 30.0
PROD_TIMEOUT_S = 2.5
PAUSE_S = 3.0
PAGE_SIZE = 20
RERANK_N = 10             # off_rerank_candidates — сколько кандидатов уходит в LLM

# Пороги правила верности (см. шапку). Меняются только вместе с отчётом.
KCAL_TOL = 0.10
NAME_TOL = 0.50
STEM_LEN = 5
WORD_MIN = 4

SEARCH_URL = "https://search.openfoodfacts.org/search"
BARCODE_URL = "https://world.openfoodfacts.org/api/v2/product/{}"
PROBE_BARCODE = "3017620422003"   # Nutella — эталон живости

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
RERANK_MODEL = "gpt-5.4-mini"

# ── Промпт переранжирования (ADR-0019 §2.3) ─────────────────────────────────
# ЭТОТ ТЕКСТ ПОРТИРУЕТСЯ В ПРОД ДОСЛОВНО. Скрипт печатает его sha256; тест
# `test_rerank_prompt_matches_probe` держит хеш прод-промпта равным этому —
# иначе замер относился бы к одному промпту, а работал бы другой.
RERANK_SYSTEM = """\
Ты сопоставляешь позиции из дневника питания с карточками товаров из базы Open Food Facts.
Для КАЖДОЙ позиции выбери карточку, описывающую ТОТ ЖЕ продукт, либо откажись.

Отказ — нормальный и ожидаемый ответ. Отказывайся, если ни одна карточка не описывает
тот же продукт или если выбор неочевиден: чужие числа КБЖУ хуже, чем их отсутствие.

Совпадать должен ПРОДУКТ, а не слова в названии:
- «сыр плавленый» и «сыр творожный» — разные продукты;
- «молоко 3.2%» и «молоко 1.5%» — разные продукты;
- «тунец в масле» и «тунец в собственном соку» — разные продукты;
- сухой продукт и он же в готовом (отварном) виде — разные продукты.
Если у позиции указан бренд и у карточки указан бренд — они должны совпадать.

Верни СТРОГО JSON-объект:
{"choices":[{"position":<номер позиции>,"candidate":<номер карточки или null>}]}
- candidate — номер карточки ИЗ СПИСКА ЭТОЙ ПОЗИЦИИ, либо null (отказ);
- ровно одна запись на каждую позицию;
- никакого текста вне JSON.\
"""

# Вторая — и ПОСЛЕДНЯЯ — формулировка. Пред-зарегистрирована до прогона, отличается
# ровно одной осью: насколько поощряется отказ. Нужна, чтобы «вариант A провалил
# гейт» не означало «я написал один промпт, и он оказался слишком осторожным».
# Больше формулировок не пробуем: подбор промпта по 25 кейсам, на которых же и
# считается метрика, — это подгонка под тестовый набор, а не замер.
RERANK_SYSTEM_B = """\
Ты сопоставляешь позиции из дневника питания с карточками товаров из базы Open Food Facts.
Для КАЖДОЙ позиции выбери карточку, описывающую тот же продукт.

Совпадать должен ПРОДУКТ, а не слова в названии:
- «сыр плавленый» и «сыр творожный» — разные продукты;
- «молоко 3.2%» и «молоко 1.5%» — разные продукты;
- «тунец в масле» и «тунец в собственном соку» — разные продукты;
- сухой продукт и он же в готовом (отварном) виде — разные продукты.
Если у позиции указан бренд и у карточки указан бренд — они должны совпадать.
Если ни одна карточка не описывает тот же продукт — верни null.

Верни СТРОГО JSON-объект:
{"choices":[{"position":<номер позиции>,"candidate":<номер карточки или null>}]}
- candidate — номер карточки ИЗ СПИСКА ЭТОЙ ПОЗИЦИИ, либо null;
- ровно одна запись на каждую позицию;
- никакого текста вне JSON.\
"""


# ── Транспорт ───────────────────────────────────────────────────────────────
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(
            req, timeout=REQUEST_TIMEOUT_S, context=ssl.create_default_context()
        ) as r:
            body = r.read().decode("utf-8", "replace")
            dt = time.monotonic() - t0
            if r.status != 200:
                return None, dt, f"HTTP {r.status}"
            return json.loads(body), dt, None
    except urllib.error.HTTPError as e:
        return None, time.monotonic() - t0, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, time.monotonic() - t0, f"сеть: {e.reason}"
    except json.JSONDecodeError:
        return None, time.monotonic() - t0, "ответ не JSON"
    except Exception as e:                       # noqa: BLE001 — диагностика
        return None, time.monotonic() - t0, f"{type(e).__name__}: {e}"


def search(terms):
    q = urllib.parse.urlencode({"q": terms, "page_size": str(PAGE_SIZE),
                                "langs": "ru,en"})
    return http_get(f"{SEARCH_URL}?{q}")


def read_env_key():
    """LLM_API_KEY из окружения либо из .env (без внешних зависимостей)."""
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(name)
        if val:
            return val
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip().upper() in ("LLM_API_KEY", "OPENAI_API_KEY"):
            val = val.strip().strip('"').strip("'")
            if val:
                return val
    return None


def openai_chat(api_key, system, user, max_tokens=1200):
    """Один вызов chat.completions. Параметры — как у прод-цели (ADR-0017 §16)."""
    payload = json.dumps({
        "model": RERANK_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        OPENAI_URL, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "User-Agent": USER_AGENT},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(
            req, timeout=REQUEST_TIMEOUT_S, context=ssl.create_default_context()
        ) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return None, time.monotonic() - t0, None, f"HTTP {e.code}: {body}"
    except Exception as e:                       # noqa: BLE001 — диагностика
        return None, time.monotonic() - t0, None, f"{type(e).__name__}: {e}"
    dt = time.monotonic() - t0
    choice = (data.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content")) or ""
    return text, dt, data.get("usage"), None


# ── Маппинг карточки (целевой, search-a-licious) ────────────────────────────
def _num(n, key):
    v = (n or {}).get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(v):
    return (v or "").strip().lower().replace("ё", "е")


def _tokens(v):
    return [t for t in re.split(r"[^0-9a-zа-я]+", _norm(v)) if t]


def _first_brand(brands):
    """В search-a-licious `brands` — СПИСОК (у 10 карточек из 20) либо его нет.
    Прод `_first_brand` умеет только CSV-строку и падает на списке — дефект
    найден этим замером (v3)."""
    if not brands:
        return ""
    if isinstance(brands, (list, tuple)):
        return str(brands[0]).strip() if brands else ""
    return str(brands).split(",")[0].strip()


def _best_name(p):
    """`product_name_ru` в search-a-licious НЕ существует (0 из 20)."""
    for k in ("product_name", "product_name_ru", "product_name_en"):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def macros_of(p):
    """Полные 4 значения или None (карточка не кандидат — фильтр применимости)."""
    n = p.get("nutriments") or {}
    kcal = _num(n, "energy-kcal_100g")
    pr, fa, ca = (_num(n, "proteins_100g"), _num(n, "fat_100g"),
                  _num(n, "carbohydrates_100g"))
    if None in (kcal, pr, fa, ca):
        return None
    return {"kcal": kcal, "protein": pr, "fat": fa, "carbs": ca}


# ── Прод-логика v3 (контрольная группа = вариант B из ADR-0019 §3) ──────────
def score_of(product, brand, name, fat_pct):
    s = 0.0
    brand_norm = _norm(brand)
    name_tokens = set(_tokens(name))
    prod_brand = _norm(_first_brand(product.get("brands")))
    prod_name = _norm(_best_name(product))
    if brand_norm and (brand_norm in prod_brand or brand_norm in prod_name):
        s += 3.0
    if name_tokens:
        s += 2.0 * (len(name_tokens & set(_tokens(_best_name(product)))) / len(name_tokens))
    if fat_pct is not None:
        pf = _num(product.get("nutriments") or {}, "fat_100g")
        if pf is not None and abs(pf - fat_pct) < 0.6:
            s += 1.0
    try:
        s += float(product.get("completeness") or 0.0)
    except (TypeError, ValueError):
        pass
    return s


def select_best(cands, brand, name, fat_pct):
    """Как в проде: минимального порога скора НЕТ, топ-1 всегда."""
    if not cands:
        return None
    return max(range(len(cands)),
               key=lambda i: score_of(cands[i], brand, name, fat_pct))


# ── Подписи верности ────────────────────────────────────────────────────────
def stems(text):
    return {t[:STEM_LEN] for t in _tokens(text) if len(t) >= WORD_MIN}


def sig_kcal(card_kcal, ref_kcal, tol=KCAL_TOL):
    return abs(card_kcal - ref_kcal) / max(ref_kcal, 1.0) <= tol


def name_coverage(card, ref_name, ref_brand):
    """Доля основ эталонного названия, покрытых названием+брендом карточки."""
    ref = stems(f"{ref_name} {ref_brand or ''}")
    if not ref:
        return 0.0
    got = stems(f"{_best_name(card)} {_first_brand(card.get('brands'))}")
    return len(ref & got) / len(ref)


def sig_name(card, ref_name, ref_brand, tol=NAME_TOL):
    return name_coverage(card, ref_name, ref_brand) >= tol


def head_match(card, ref_name):
    """Альтернативное правило имени (для §ЧУВСТВИТЕЛЬНОСТЬ): совпала ли основа
    ГЛАВНОГО слова эталона — первого слова длиной ≥ 4."""
    heads = [t[:STEM_LEN] for t in _tokens(ref_name) if len(t) >= WORD_MIN]
    if not heads:
        return False
    got = stems(f"{_best_name(card)} {_first_brand(card.get('brands'))}")
    return heads[0] in got


def verdict(card, ref, kcal_tol=KCAL_TOL, name_tol=NAME_TOL, name_rule="coverage"):
    """ВЕРНО | СПОРНО | ОШИБКА по двум подписям."""
    m = macros_of(card)
    k = sig_kcal(m["kcal"], ref["kcal"], kcal_tol) if m else False
    if name_rule == "head":
        n = head_match(card, ref["name"])
    elif name_rule == "either":
        n = sig_name(card, ref["name"], ref["brand"], name_tol) or head_match(card, ref["name"])
    else:
        n = sig_name(card, ref["name"], ref["brand"], name_tol)
    if k and n:
        return "ВЕРНО"
    return "СПОРНО" if (k or n) else "ОШИБКА"


# ── Вход: кейсы выводятся из manifest + сохранённого прогона ────────────────
def load_cases(run_path):
    """(id, запрос модели, эталон) — ничего не копируется руками (см. шапку)."""
    try:
        import yaml
    except ImportError:
        print("нужен PyYAML (он в requirements.txt): pip install PyYAML")
        raise
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    truth = {}
    for case in manifest:
        if case.get("tier") != "A" or case.get("status"):
            continue
        gt = case["ground_truth"]
        kcal = float(gt["kcal"])
        if gt.get("basis") == "per_portion":
            # ИСПРАВЛЕНИЕ v4: карточка OFF даёт КБЖУ на 100 г. Сравнение с
            # эталоном «на упаковку» делало кейс непроходимым в принципе.
            grams = float(gt["portion_grams"])
            kcal = kcal * 100.0 / grams
        truth[case["id"]] = {"kcal": kcal, "name": gt["name"], "brand": gt.get("brand"),
                             "basis": gt.get("basis")}

    run = json.loads(pathlib.Path(run_path).read_text(encoding="utf-8"))
    cases = []
    for rec in run["records"]:
        cid = rec["id"]
        if cid not in truth:
            continue
        items = (rec["runs"][0] or {}).get("items") or []
        if not items:
            continue
        it = items[0]
        cases.append({
            "id": cid,
            "brand": it.get("brand"),
            "name": it.get("name"),
            "fat_pct": it.get("fat_pct"),
            "barcode": it.get("barcode"),
            # Извлекатель сам опознал продукт верно? Если нет — запрос к источнику
            # заведомо не тот, и промах матчинга к нему отношения не имеет.
            # Без этой пометки провал гейта нельзя отделить от чужой ошибки.
            "name_ok": bool((rec.get("scores") or [{}])[0].get("name_ok")),
            "ref": truth[cid],
        })
    return sorted(cases, key=lambda c: c["id"])


def attach_name_ok(rows, run_path):
    """Проставляет `name_ok` в строки, поднятые из кэша (там его может не быть)."""
    try:
        run = json.loads(pathlib.Path(run_path).read_text(encoding="utf-8"))
    except OSError:
        return
    flags = {rec["id"]: bool((rec.get("scores") or [{}])[0].get("name_ok"))
             for rec in run.get("records", [])}
    for row in rows:
        row.setdefault("name_ok", flags.get(row["id"], True))


def terms_of(case):
    fat = f"{case['fat_pct']:g}%" if case["fat_pct"] is not None else None
    return " ".join(p for p in (case["brand"], case["name"], fat) if p)


# ── ЭТАП 1. Разведка ────────────────────────────────────────────────────────
def probe():
    print("=" * 78)
    print("ЭТАП 1 — РАЗВЕДКА")
    print("=" * 78)
    data, dt, err = search("кефир 3.2%")
    if err:
        print(f"  search-a-licious: ✗ {err} ({dt:.2f} с) — дальше идти некуда")
        return False, False
    hits = data.get("hits") or []
    print(f"  search-a-licious: ✓ 200 OK, {len(hits)} карточек ({dt:.2f} с)")
    if hits:
        for key in ("product_name", "product_name_ru", "brands", "completeness",
                    "code", "_score"):
            have = sum(1 for x in hits if x.get(key) not in (None, "", [], {}))
            types = {type(x.get(key)).__name__ for x in hits if x.get(key) is not None}
            print(f"    {key:16} {have:>2}/{len(hits)}  тип: {'/'.join(sorted(types)) or '—'}")
        print(f"    {'полное КБЖУ':16} {sum(1 for x in hits if macros_of(x)):>2}/{len(hits)}")
    time.sleep(2)
    data, dt, err = http_get(BARCODE_URL.format(PROBE_BARCODE)
                             + "?fields=code,product_name,brands,nutriments")
    if err:
        print(f"  barcode /api/v2: ✗ {err} ({dt:.2f} с)")
        return True, False
    prod = data.get("product") or {}
    print(f"  barcode /api/v2 : ✓ «{_best_name(prod)[:38]}» ({dt:.2f} с)")
    return True, True


# ── ЭТАП 2. Сбор выдачи (один сетевой проход на оба матчера) ────────────────
def collect(cases):
    print("\n" + "=" * 78)
    print("ЭТАП 2 — СБОР ВЫДАЧИ (одна выдача на оба механизма выбора)")
    print("=" * 78)
    rows, lats = [], []
    for idx, case in enumerate(cases, 1):
        terms = terms_of(case)
        data, dt, err = search(terms)
        lats.append(dt)
        if err:
            print(f"[{idx:2}/{len(cases)}] {case['id']}  ОШИБКА: {err} ({dt:.2f} с)")
            rows.append({**case, "error": err, "candidates": [], "latency_s": dt})
        else:
            hits = data.get("hits") or []
            # Полный список пригодных карточек СОХРАНЯЕТСЯ целиком: без него
            # нельзя отличить «переранжирование слабое» от «N=10 слишком мал»,
            # то есть нельзя понять, чинится ли провал гейта настройкой N.
            usable = [h for h in hits if macros_of(h) is not None]
            cands = usable[:RERANK_N]
            in_top = [i for i, c in enumerate(cands) if verdict(c, case["ref"]) == "ВЕРНО"]
            in_all = [i for i, c in enumerate(usable) if verdict(c, case["ref"]) == "ВЕРНО"]
            print(f"[{idx:2}/{len(cases)}] {case['id']}  «{terms[:40]}»  "
                  f"{len(hits)} hits → {len(usable)} пригодных, верных: "
                  f"топ-{RERANK_N} {len(in_top)}, вся выдача {len(in_all)}")
            rows.append({**case, "error": None, "candidates": cands, "usable": usable,
                         "latency_s": dt, "right_top": in_top, "right_all": in_all})
        if idx < len(cases):
            time.sleep(PAUSE_S)
    return rows, lats


# ── ЭТАП 3. Переранжирование ────────────────────────────────────────────────
def render_batch(batch, with_numbers=True):
    """User-часть запроса. Нумерация позиций и карточек — с единицы."""
    out = []
    for pi, row in enumerate(batch, 1):
        head = f"Позиция {pi}: {row['name']}"
        if row.get("brand"):
            head += f" | бренд: {row['brand']}"
        if row.get("fat_pct") is not None:
            head += f" | жирность: {row['fat_pct']:g}%"
        out.append(head)
        out.append("Карточки:")
        for ci, card in enumerate(row["candidates"], 1):
            m = macros_of(card)
            line = f"  {ci}. {_best_name(card)}"
            brand = _first_brand(card.get("brands"))
            if brand:
                line += f" | бренд: {brand}"
            if with_numbers:
                line += (f" | {m['kcal']:g} ккал, Б {m['protein']:g} "
                         f"Ж {m['fat']:g} У {m['carbs']:g}")
            out.append(line)
        out.append("")
    return "\n".join(out)


def parse_choices(text, n_positions):
    """{"choices":[{"position":1,"candidate":3|null}]} → {позиция: индекс|None}."""
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    except Exception:                            # noqa: BLE001 — кривой ответ = отказ
        return {}
    out = {}
    for ch in data.get("choices") or []:
        try:
            pos = int(ch["position"])
        except (KeyError, TypeError, ValueError):
            continue
        cand = ch.get("candidate")
        if cand is None or (isinstance(cand, str) and not cand.strip().isdigit()):
            out[pos] = None
        else:
            try:
                out[pos] = int(cand) - 1
            except (TypeError, ValueError):
                out[pos] = None
    return {p: v for p, v in out.items() if 1 <= p <= n_positions}


def rerank(rows, api_key, mode, batch_size, with_numbers, system=RERANK_SYSTEM):
    """Возвращает {case_id: выбранный индекс кандидата | None} + телеметрию."""
    live = [r for r in rows if r["candidates"]]
    picks, calls = {}, []
    for start in range(0, len(live), batch_size):
        batch = live[start:start + batch_size]
        user = render_batch(batch, with_numbers)
        text, dt, usage, err = openai_chat(api_key, system, user)
        if err:
            print(f"    [{mode}] батч {start // batch_size + 1}: ОШИБКА {err}")
            for row in batch:
                picks[row["id"]] = None          # сбой вызова = отказ ступени
            calls.append({"error": err, "latency_s": dt})
            continue
        chosen = parse_choices(text, len(batch))
        for pi, row in enumerate(batch, 1):
            idx = chosen.get(pi)
            if idx is not None and not (0 <= idx < len(row["candidates"])):
                idx = None                        # номер вне диапазона = отказ
            picks[row["id"]] = idx
        calls.append({"error": None, "latency_s": dt, "usage": usage,
                      "n_positions": len(batch), "raw": text})
        print(f"    [{mode}] батч {start // batch_size + 1}: "
              f"{len(batch)} позиц. за {dt:.2f} с")
    for row in rows:
        picks.setdefault(row["id"], None)
    return picks, calls


# ── ЭТАП 4. Сводка ──────────────────────────────────────────────────────────
def classify(rows, picks, **kw):
    res = []
    for row in rows:
        ref = row["ref"]
        cands, usable = row["candidates"], row.get("usable") or row["candidates"]
        idx = picks.get(row["id"])
        card = cands[idx] if idx is not None and idx < len(cands) else None
        res.append({
            "id": row["id"],
            # Верная карточка ДОСТУПНА переранжированию (топ-N) — знаменатель гейта.
            "present": any(verdict(c, ref, **kw) == "ВЕРНО" for c in cands),
            # Верная карточка есть где-то в выдаче — отделяет слабый матчинг от малого N.
            "present_all": any(verdict(c, ref, **kw) == "ВЕРНО" for c in usable),
            # Определение ADR-0019 §6 дословно («по близости ккал») — для сверки
            # с прежним замером, где подпись была одна.
            "present_kcal": any(sig_kcal(macros_of(c)["kcal"], ref["kcal"]) for c in usable),
            "verdict": "ОТКАЗ" if card is None else verdict(card, ref, **kw),
            "card": card,
        })
    return res


def gate(res):
    """(верных, знаменатель-present, знаменатель-все, уверенных ошибок, спорных)."""
    present = [r for r in res if r["present"]]
    right = [r for r in present if r["verdict"] == "ВЕРНО"]
    confident_errors = [r for r in res if r["verdict"] == "ОШИБКА"]
    disputed = [r for r in res if r["verdict"] == "СПОРНО"]
    return len(right), len(present), len(res), len(confident_errors), len(disputed)


def upper_bound(res):
    """Потолок: КАЖДОЕ «спорно» засчитано верным. Если и он ниже порога — спор
    о правиле имени на вердикт гейта не влияет, и калибровать его незачем."""
    disp = [r for r in res if r["verdict"] == "СПОРНО"]
    right = len([r for r in res if r["present"] and r["verdict"] == "ВЕРНО"]) + len(disp)
    den = len([r for r in res if r["present"]]) + len(disp)
    return right, den


def report(rows, results, lats, baseline, barcode_ok):
    print("\n" + "=" * 78)
    print("ИТОГИ")
    print("=" * 78)

    n = len(rows)
    probe = classify(rows, {})
    print(f"\n  кейсов                   : {n}")
    print(f"  ошибок запроса к источнику: {sum(1 for r in rows if r['error'])}")
    print(f"  верная карточка (2 подписи) в топ-{RERANK_N} : "
          f"{sum(1 for r in probe if r['present'])}/{n}   ← знаменатель гейта")
    print(f"  верная карточка (2 подписи) во всей выдаче: "
          f"{sum(1 for r in probe if r['present_all'])}/{n}"
          f"   ← разница с предыдущим = цена отсечки N={RERANK_N}")
    print(f"  карточка с близкими ккал во всей выдаче  : "
          f"{sum(1 for r in probe if r['present_kcal'])}/{n}"
          f"   ← определение ADR-0019 §6 (одна подпись)")
    s = sorted(lats)
    print(f"  латентность поиска       : медиана {s[len(s) // 2]:.2f} с, "
          f"максимум {max(s):.2f} с; вне прод-лимита {PROD_TIMEOUT_S} с: "
          f"{sum(1 for x in lats if x > PROD_TIMEOUT_S)}/{n}")
    print(f"  barcode-путь             : {'РАБОТАЕТ' if barcode_ok else 'НЕ РАБОТАЕТ'}")

    for mode, payload in results.items():
        res = payload["res"]
        right, den2, den1, errs, disp = gate(res)
        print(f"\n  ── {mode} " + "─" * (68 - len(mode)))
        print(f"    ВЕРНО {right} · СПОРНО {disp} · УВЕРЕННАЯ ОШИБКА {errs} · "
              f"ОТКАЗ {sum(1 for r in res if r['verdict'] == 'ОТКАЗ')}")
        acc2 = f"{right / den2:.0%}" if den2 else "—"
        print(f"    знаменатель (2) верная карточка в топ-{RERANK_N}: "
              f"{right}/{den2} = {acc2}   ← порог ADR-0019 §6 ≥80%")
        print(f"    знаменатель (1) все кейсы прогона          : "
              f"{right}/{den1} = {right / den1:.0%}")
        ub_r, ub_d = upper_bound(res)
        print(f"    ПОТОЛОК (всё «спорно» засчитано верным)    : "
              f"{ub_r}/{ub_d} = " + (f"{ub_r / ub_d:.0%}" if ub_d else "—"))
        # Самый благоприятный для варианта A счёт: «спорно» = верно И сняты кейсы,
        # где извлекатель сам назвал продукт неверно (запрос заведомо не тот).
        bad_q = {r["id"] for r in rows if not r.get("name_ok", True)}
        fair = [r for r in res if r["id"] not in bad_q]
        fr, fd = upper_bound(fair)
        fe = len([r for r in fair if r["verdict"] == "ОШИБКА"])
        print(f"    ПОТОЛОК без кривых запросов ({len(bad_q)} снято)      : "
              f"{fr}/{fd} = " + (f"{fr / fd:.0%}" if fd else "—")
              + f", уверенных ошибок {fe}")
        ok = bool(den2) and right / den2 >= 0.80 and errs == 0
        why = [] if ok else (
            ([f"точность {acc2} < 80%"] if not (den2 and right / den2 >= 0.80) else [])
            + ([f"уверенных ошибок {errs} ≠ 0"] if errs else [])
        )
        print(f"    ГЕЙТ: {'ВЗЯТ' if ok else 'НЕ ВЗЯТ — ' + '; '.join(why)}")

    print("\n  ── КОНТРОЛЬНЫЕ ГРУППЫ (ни одного вызова модели) ──")
    print("  Нужны, чтобы метрика не оказалась константой: если «всегда топ-1» и")
    print("  «всегда последний» дают тот же результат, что переранжирование, то")
    print("  замеряется не механизм выбора, а состав выдачи.")
    controls = {
        "select_best (вариант B)": {
            r["id"]: select_best(r["candidates"], r["brand"], r["name"], r["fat_pct"])
            for r in rows
        },
        "всегда топ-1 по _score": {
            r["id"]: (0 if r["candidates"] else None) for r in rows
        },
        "всегда последний кандидат": {
            r["id"]: (len(r["candidates"]) - 1 if r["candidates"] else None)
            for r in rows
        },
    }
    for label, picks in controls.items():
        res = baseline if label.startswith("select_best") else classify(rows, picks)
        right, den2, den1, errs, disp = gate(res)
        acc = f"{right / den2:.0%}" if den2 else "—"
        print(f"    {label:<28} ВЕРНО {right} · СПОРНО {disp} · ОШИБКА {errs:>2} · "
              f"{right}/{den2} = {acc}")

    print("\n  ── ЧУВСТВИТЕЛЬНОСТЬ (тот же прогон, другие правила верности) ──")
    print(f"  {'правило':<34}", " ".join(f"{m:>14}" for m in results))
    variants = [
        ("базовое: ккал≤10%, покрытие≥0.5", {}),
        ("ккал ≤5%", {"kcal_tol": 0.05}),
        ("ккал ≤15%", {"kcal_tol": 0.15}),
        ("покрытие ≥0.34", {"name_tol": 0.34}),
        ("имя: главное слово", {"name_rule": "head"}),
        ("имя: покрытие ИЛИ главное слово", {"name_rule": "either"}),
    ]
    for label, kw in variants:
        cells = []
        for mode, payload in results.items():
            res = classify(rows, payload["picks"], **kw)
            right, den2, den1, errs, _ = gate(res)
            acc = f"{right}/{den2}" + (f"={right / den2:.0%}" if den2 else "")
            cells.append(f"{acc}{'!' if errs else ''}")
        print(f"  {label:<34}", " ".join(f"{c:>14}" for c in cells))
    print("  «!» — есть уверенные ошибки, гейт не взят независимо от точности.")

    print("\n  ── ПОКЕЙСОВО (для ручной перепроверки каждой строки) ──")
    for mode, payload in results.items():
        res = payload["res"]
        print(f"\n  [{mode}]")
        by_id = {r["id"]: r for r in res}
        for row in rows:
            r = by_id[row["id"]]
            ref = row["ref"]
            card = r["card"]
            got = (f"«{_best_name(card)[:36]}» {macros_of(card)['kcal']:g} ккал"
                   if card else "— (отказ)")
            cov = f"{name_coverage(card, ref['name'], ref['brand']):.2f}" if card else "—"
            print(f"    {row['id']:6} {r['verdict']:<7} "
                  f"эталон {ref['kcal']:>6.1f} «{ref['name'][:30]}»")
            print(f"           выбрано: {got}  покрытие имени {cov}  "
                  f"{'верная карточка В ВЫДАЧЕ' if r['present'] else 'верной карточки в выдаче НЕТ'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="только сбор выдачи")
    ap.add_argument("--cache", help="взять выдачу из JSON прошлого прогона")
    ap.add_argument("--run", default=str(DEFAULT_RUN), help="прогон-источник запросов")
    ap.add_argument("--out", default=str(PROBES_DIR / "off-rerank.json"))
    args = ap.parse_args()
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    import hashlib
    for name, text in (("A", RERANK_SYSTEM), ("B", RERANK_SYSTEM_B)):
        print(f"промпт {name} sha256: {hashlib.sha256(text.encode()).hexdigest()}")
    print()

    cached_rerank = {}
    if args.cache:
        cached = json.loads(pathlib.Path(args.cache).read_text(encoding="utf-8"))
        rows, lats, barcode_ok = cached["rows"], cached["latencies"], cached["barcode_ok"]
        cached_rerank = cached.get("rerank") or {}
        print(f"выдача взята из кэша {args.cache} ({len(rows)} кейсов, "
              f"режимов переранжирования: {len(cached_rerank)})")
    else:
        search_ok, barcode_ok = probe()
        if not search_ok:
            print("\n❌ search-a-licious недоступен — замер невозможен.")
            return 1
        cases = load_cases(args.run)
        rows, lats = collect(cases)
        pathlib.Path(args.out).write_text(
            json.dumps({"rows": rows, "latencies": lats, "barcode_ok": barcode_ok},
                       ensure_ascii=False), encoding="utf-8")
        print(f"\nвыдача сохранена: {args.out}")
    attach_name_ok(rows, args.run)

    baseline = classify(
        rows,
        {r["id"]: select_best(r["candidates"], r["brand"], r["name"], r["fat_pct"])
         for r in rows},
    )

    results = {}
    if cached_rerank:
        # Отчёт пересобирается из сохранённых выборов без единого нового вызова:
        # правило верности можно перепроверять сколько угодно, а числа модели
        # при этом остаются теми же, что в отчёте.
        for mode, payload in cached_rerank.items():
            picks = {k: v for k, v in payload["picks"].items()}
            results[mode] = {"picks": picks, "calls": payload.get("calls", []),
                             "res": classify(rows, picks)}
    elif not args.no_llm:
        api_key = read_env_key()
        if not api_key:
            print("нет LLM_API_KEY — переранжирование пропущено")
        else:
            print("\n" + "=" * 78)
            print("ЭТАП 3 — LLM-ПЕРЕРАНЖИРОВАНИЕ")
            print("=" * 78)
            # Три режима: прод-форма (батч на разбор), её вырожденный случай
            # (одна позиция) и абляция без чисел. Абляция нужна потому, что
            # подпись-ккал и числа в промпте делят информацию: если точность
            # без чисел та же, подозрение на «выбор по ккал» снимается.
            for label, batch_size, with_numbers, sys_prompt in (
                ("одна позиция", 1, True, RERANK_SYSTEM),
                ("батч по 5", 5, True, RERANK_SYSTEM),
                ("без чисел", 1, False, RERANK_SYSTEM),
                ("батч по 5, промпт B", 5, True, RERANK_SYSTEM_B),
            ):
                picks, calls = rerank(rows, api_key, label, batch_size,
                                      with_numbers, sys_prompt)
                results[label] = {"picks": picks, "calls": calls,
                                  "res": classify(rows, picks)}
            pathlib.Path(args.out).write_text(
                json.dumps({"rows": rows, "latencies": lats, "barcode_ok": barcode_ok,
                            "rerank": {m: {"picks": p["picks"], "calls": p["calls"]}
                                       for m, p in results.items()}},
                           ensure_ascii=False), encoding="utf-8")

    report(rows, results, lats, baseline, barcode_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
