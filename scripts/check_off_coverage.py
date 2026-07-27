#!/usr/bin/env python3
"""check_off_coverage.py — диагностика ступени OFF с прод-машины (v2).

ЧТО СЛУЧИЛОСЬ (прогон v1, 2026-07-27, VPS skylineNiderland):
  18 из 25 запросов к `/cgi/search.pl` вернули HTTP 503 за 0.06 с — это не поиск,
  а мгновенный отказ. Причина найдена: легаси-бэкенд поиска OFF (Perl), на котором
  построены и `/cgi/search.pl`, и `/api/v2/search`, признан устаревшим и отдаёт 503
  массово. Замена — search-a-licious (Elasticsearch) на search.openfoodfacts.org,
  статус beta. Barcode-путь `/api/v2/product/{code}` при этом работает.

ПОЧЕМУ ЭТО ВАЖНО ДЛЯ ПРОДА: `OpenFoodFactsSource` ходит ровно в `/cgi/search.pl`.
503 → SourceError → warning в лог → следующая ступень. То есть лестница ADR-0013
в проде, вероятно, уже давно сводится к label→estimate, и это никем не замечено —
наблюдаемости нет (дефект D12, `spec-nutrition-llm-contract.md` §9).

ЭТОТ СКРИПТ отвечает на три вопроса:
  ЭТАП 1 — РАЗВЕДКА: какой из четырёх эндпоинтов вообще отвечает 200 и какую форму
           ответа отдаёт (форму печатаем — она нужна, чтобы переписать адаптер).
  ЭТАП 2 — ПОКРЫТИЕ: на живом эндпоинте прогоняем 25 реальных товаров яруса A.
  ЭТАП 3 — ВЫВОД: сводка + что это значит для решения OQ-1 и приоритета R2.

Только стандартная библиотека. Ничего не пишет и не меняет — только GET.
Запуск:  python3 scripts/check_off_coverage.py
Время:   ~4 минуты.
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Настройки ───────────────────────────────────────────────────────────────
# User-Agent по требованию OFF: AppName/Version (ContactEmail).
# ВНИМАНИЕ: в проде сейчас `lifeManager_bot (skyline987650@gmail.com)` — без версии,
# то есть формат нарушен. Здесь шлём правильный, чтобы отделить проблему формата
# от проблемы самого эндпоинта.
USER_AGENT = "lifeManager_bot/1.0 (skyline987650@gmail.com)"
REQUEST_TIMEOUT_S = 15.0
PROD_TIMEOUT_S = 2.5      # off_timeout_s в конфиге прода
PAUSE_S = 7.0             # лимит OFF: 10 запросов/мин на IP для поиска
RETRY_ON_503_S = 20.0     # одна повторная попытка после 503

PROBE_QUERY = "кефир"
PROBE_BARCODE = "3017620422003"   # Nutella — заведомо есть в OFF, эталон живости

# (id, бренд, название, жирность %, эталонные ккал/100 г)
# Запросы = то, что реально выдаёт прод-модель (runs/2026-07-27_gpt-5.4-mini.json)
CASES = [
    ('A-04', 'Double B', 'сироп карамельный', None, 222.5),
    ('A-05', None, 'сыр плавленый фетакса', 45.0, 213),
    ('A-06', 'Классика вкуса', 'картофель отварной с укропом', None, 72.5),
    ('A-09', 'BB', 'картофельное пюре', None, 695),
    ('A-13', 'ВкусВилл', 'онигири с запечённым тунцом', None, 176.1),
    ('A-15', 'Черкизовский', 'корейка свиная варено-копченая', None, 110),
    ('A-16', 'Унагранде', 'сыр мягкий творожный', 65.0, 210.9),
    ('A-17', 'Русагро', 'белок яичный жидкий пастеризованный', None, 56),
    ('A-18', 'Чудо', 'коктейль молочный стерилизованный со вкусом клубники', 2.0, 71),
    ('A-19', None, 'томатная паста', None, 90),
    ('A-20', None, 'кефир', 3.2, 57),
    ('A-21', None, 'молоко питьевое пастеризованное', 3.2, 60),
    ('A-26', 'Черкизово', 'стейк на кости в пряных травах и специях', None, 221),
    ('A-27', 'ВкусВилл', 'суп с куриными фрикадельками и пастой в виде звездочек', None, 32.6),
    ('A-28', 'BB', 'сметана', 20.0, 203.6),
    ('A-29', 'Воронеж ТМ', 'карпаччо из индейки', None, 150),
    ('A-30', 'Bombbar', 'печенье протеиновое', None, 447),
    ('A-31', None, 'снеки кукурузные со вкусом сыра', None, 432),
    ('A-32', None, 'тортилья пшеничная', None, 290),
    ('A-33', 'ВВ в балансе', 'щавелевый суп с куриной грудкой', None, 44.1),
    ('A-34', 'ВкусВилл', 'творог мягкий высокобелковый', 1.5, 72.3),
    ('A-35', None, 'творог со сливками с наполнителем черника', 5.0, 140.4),
    ('A-36', None, 'рыбные консервы филе тунца в масле', None, 188),
    ('A-37', 'Квалитет', 'тунец натуральный консервированный', None, 107),
    ('A-39', 'Макфа', 'макаронные изделия спиральки', None, 342),
]
BLIND = {'A-05', 'A-13'}   # кейсы, где проверки C2–C4 слепы (§7 ТЗ)

LEGACY_FIELDS = (
    "code,product_name,product_name_ru,brands,quantity,"
    "nutrition_data_per,nutriments,completeness"
)


# ── Транспорт ───────────────────────────────────────────────────────────────
def http_get(url):
    """GET → (данные|None, латентность, ошибка|None)."""
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
        return None, time.monotonic() - t0, "ответ не JSON (вероятно HTML-заглушка)"
    except Exception as e:                       # noqa: BLE001 — диагностика
        return None, time.monotonic() - t0, f"{type(e).__name__}: {e}"


# ── Построители URL под каждый эндпоинт ─────────────────────────────────────
def url_legacy(terms):
    p = {"search_terms": terms, "search_simple": "1", "action": "process",
         "json": "1", "page_size": "20", "lc": "ru", "fields": LEGACY_FIELDS}
    return "https://world.openfoodfacts.org/cgi/search.pl?" + urllib.parse.urlencode(p)


def url_v2(terms):
    p = {"categories_tags_ru": terms, "fields": LEGACY_FIELDS, "page_size": "20"}
    return "https://world.openfoodfacts.org/api/v2/search?" + urllib.parse.urlencode(p)


def url_salicious(terms):
    p = {"q": terms, "page_size": "20", "langs": "ru,en"}
    return "https://search.openfoodfacts.org/search?" + urllib.parse.urlencode(p)


ENDPOINTS = [
    ("legacy /cgi/search.pl   (используется в проде)", url_legacy),
    ("world /api/v2/search    (тот же Perl-бэкенд)", url_v2),
    ("search-a-licious        (рекомендованная замена, beta)", url_salicious),
]


# ── Разбор ответа: две разные формы ─────────────────────────────────────────
def extract_hits(data):
    """Легаси отдаёт `products`, search-a-licious — `hits`. Возвращаем список."""
    if not isinstance(data, dict):
        return []
    return data.get("products") or data.get("hits") or []


def num(n, key):
    try:
        v = n.get(key)
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def kcal_of(product):
    """Повторяет map_product: ккал напрямую либо из кДж; нужны все 4 значения."""
    n = product.get("nutriments") or {}
    kcal = num(n, "energy-kcal_100g")
    if kcal is None:
        kj = num(n, "energy-kj_100g")
        kcal = round(kj / 4.184, 1) if kj is not None else None
    p, f, c = num(n, "proteins_100g"), num(n, "fat_100g"), num(n, "carbohydrates_100g")
    return None if None in (kcal, p, f, c) else kcal


def best_name(product):
    return (product.get("product_name_ru") or product.get("product_name") or "").strip()


def search_terms(brand, name, fat_pct):
    fat = f"{fat_pct:g}%" if fat_pct is not None else None
    return " ".join(p for p in (brand, name, fat) if p)


# ── ЭТАП 1. Разведка ────────────────────────────────────────────────────────
def probe():
    print("=" * 78)
    print("ЭТАП 1 — РАЗВЕДКА: какой эндпоинт OFF жив")
    print(f"User-Agent: {USER_AGENT}")
    print("=" * 78)

    alive = []
    for label, builder in ENDPOINTS:
        print(f"\n  {label}")
        data, dt, err = http_get(builder(PROBE_QUERY))
        if err:
            print(f"    ✗ {err}   ({dt:.2f} с)")
        else:
            hits = extract_hits(data)
            print(f"    ✓ 200 OK, найдено {len(hits)} карточек   ({dt:.2f} с)")
            print(f"      ключи ответа: {sorted(data.keys())[:8]}")
            if hits:
                h = hits[0]
                print(f"      ключи карточки: {sorted(h.keys())[:10]}")
                print(f"      КБЖУ читается: "
                      f"{'да, ' + str(kcal_of(h)) + ' ккал' if kcal_of(h) else 'НЕТ'}")
                if not kcal_of(h):
                    n = h.get("nutriments") or {}
                    print(f"      ключи nutriments: {sorted(n.keys())[:12]}")
            alive.append((label, builder))
        time.sleep(3)

    print("\n  barcode /api/v2/product/{code} — отдельный путь, поиск не задействует")
    data, dt, err = http_get(
        f"https://world.openfoodfacts.org/api/v2/product/{PROBE_BARCODE}"
        f"?fields={LEGACY_FIELDS}"
    )
    if err:
        print(f"    ✗ {err}   ({dt:.2f} с)")
        barcode_ok = False
    else:
        prod = data.get("product") or {}
        k = kcal_of(prod)
        print(f"    ✓ 200 OK: «{best_name(prod)[:40]}» "
              f"{'КБЖУ ' + str(k) + ' ккал' if k else 'без полного КБЖУ'}   ({dt:.2f} с)")
        barcode_ok = True

    return alive, barcode_ok


# ── ЭТАП 2. Покрытие ────────────────────────────────────────────────────────
def measure(label, builder):
    print("\n" + "=" * 78)
    print(f"ЭТАП 2 — ПОКРЫТИЕ на: {label.strip()}")
    print("=" * 78)

    rows, lats = [], []
    for idx, (cid, brand, name, fat, ref) in enumerate(CASES, 1):
        terms = search_terms(brand, name, fat)
        mark = "  <— слепой кейс" if cid in BLIND else ""
        print(f"\n[{idx:2}/{len(CASES)}] {cid}{mark}\n    запрос: {terms}")

        data, dt, err = http_get(builder(terms))
        if err and "503" in err:
            print(f"    503 — повтор через {RETRY_ON_503_S:.0f} с…")
            time.sleep(RETRY_ON_503_S)
            data, dt, err = http_get(builder(terms))
        lats.append(dt)

        if err:
            print(f"    ОШИБКА: {err}  ({dt:.2f} с)")
            rows.append((cid, "ОШИБКА", err, dt))
        else:
            hits = extract_hits(data)
            full = [h for h in hits if kcal_of(h) is not None]
            if not hits:
                verdict, detail = "НЕ НАЙДЕН", "выдача пуста"
            elif not full:
                verdict = "БЕЗ КБЖУ"
                detail = f"{len(hits)} карточек, ни одной с полным КБЖУ"
            else:
                got = kcal_of(full[0])
                diff = abs(got - ref) / max(ref, 1)
                verdict = "СОШЁЛСЯ" if diff <= 0.15 else "РАСХОЖДЕНИЕ"
                detail = (f"«{best_name(full[0])[:38]}» {got} vs эталон {ref} "
                          f"(откл. {diff:.0%}); полных {len(full)}/{len(hits)}")
            slow = "  ⚠ дольше прод-лимита" if dt > PROD_TIMEOUT_S else ""
            print(f"    {verdict}: {detail}\n    время: {dt:.2f} с{slow}")
            rows.append((cid, verdict, detail, dt))

        if idx < len(CASES):
            time.sleep(PAUSE_S)
    return rows, lats


# ── ЭТАП 3. Сводка ──────────────────────────────────────────────────────────
def report(rows, lats, barcode_ok, endpoint_label):
    print("\n" + "=" * 78)
    print("ИТОГИ")
    print("=" * 78)
    n = len(rows)
    cnt = lambda v: sum(1 for r in rows if r[1] == v)   # noqa: E731
    found = cnt("СОШЁЛСЯ") + cnt("РАСХОЖДЕНИЕ")
    err_n = cnt("ОШИБКА")
    valid = n - err_n

    print(f"\n  эндпоинт             : {endpoint_label.strip()}")
    print(f"  всего товаров        : {n}")
    print(f"  ошибок запроса       : {err_n}   ← если >20%, замер недействителен")
    print(f"  измерено фактически  : {valid}")
    if valid:
        print(f"  найдено с полным КБЖУ: {found}  ({found / valid:.0%} от измеренных)")
        print(f"    сошлось ≤15%       : {cnt('СОШЁЛСЯ')}")
        print(f"    расхождение >15%   : {cnt('РАСХОЖДЕНИЕ')}")
    print(f"  найдено без КБЖУ     : {cnt('БЕЗ КБЖУ')}")
    print(f"  не найдено           : {cnt('НЕ НАЙДЕН')}")

    s = sorted(lats)
    slow = sum(1 for x in lats if x > PROD_TIMEOUT_S)
    print(f"\n  латентность: медиана {s[len(s) // 2]:.2f} с, максимум {max(s):.2f} с")
    print(f"  не уложились в прод-лимит {PROD_TIMEOUT_S} с: {slow}/{n}")
    print(f"  barcode-путь /api/v2/product: {'РАБОТАЕТ' if barcode_ok else 'НЕ РАБОТАЕТ'}")

    print("\n  Слепые кейсы (ради них вводилась сверка, §7 ТЗ):")
    for cid, verdict, detail, _ in rows:
        if cid in BLIND:
            print(f"    {cid}: {verdict} — {detail}")
    print()


def main():
    alive, barcode_ok = probe()
    if not alive:
        print("\n" + "=" * 78)
        print("❌ НИ ОДИН ПОИСКОВЫЙ ЭНДПОИНТ OFF НЕ ОТВЕЧАЕТ")
        print("=" * 78)
        print("  Замер покрытия невозможен. Для проекта это означает, что сетевая")
        print("  ступень лестницы ADR-0013 в проде не работает вообще, и резолвер")
        print("  фактически сводится к label→estimate.")
        bc = ("работает — остаётся единственным входом в OFF"
              if barcode_ok else "тоже не работает")
        print(f"  barcode-путь: {bc}")
        return 1

    label, builder = alive[-1]   # предпочитаем последний живой (search-a-licious)
    rows, lats = measure(label, builder)
    report(rows, lats, barcode_ok, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
