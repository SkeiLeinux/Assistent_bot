#!/usr/bin/env python3
"""check_off_coverage.py — замер покрытия RU-товаров в Open Food Facts с прод-машины.

Зачем: решение OQ-1 (`04_specs/spec-nutrition-llm-contract.md` §7, ревизия ADR-0013) —
сверять label-позиции с внешним источником — работает только если товар в OFF есть.
Калибровка проверок (`05_implementation/llm-checks-calibration-2026-07-27.md` §3)
показала, что сверка с OFF — ЕДИНСТВЕННЫЙ механизм против 63% остаточных ошибок:
проверки C2–C4 ловят 0 из 2 ошибок выбранной модели. Значит цена вопроса высокая,
а покрытие не измерено (§12.4 ТЗ).

Что меряет — три вещи за один прогон:
  1. ДОСТУПНОСТЬ  — отвечает ли OFF с этой машины вообще.
  2. ПОКРЫТИЕ     — сколько из 25 реальных товаров находится, и с полным ли КБЖУ.
  3. ТАЙМАУТ      — сколько запросов НЕ уложились бы в прод-лимит off_timeout_s=2.5 с.

Запросы — те же, что уйдут в проде: имя/бренд/жирность взяты из сохранённого прогона
`tests/eval/runs/2026-07-27_gpt-5.4-mini.json`, то есть это ровно то, что выдаёт
выбранная vision-модель, а не полные названия из эталона.

Только стандартная библиотека — ставить ничего не нужно, venv не нужен.
Ничего не пишет и не меняет: только GET-запросы к публичному API.

Запуск:  python3 check_off_coverage.py
Время:   ~3 минуты (пауза 7 с между запросами — лимит OFF ~10 запросов в минуту).
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Настройки (менять не нужно) ─────────────────────────────────────────────
BASE_URL = "https://world.openfoodfacts.org"
USER_AGENT = "lifeManager_bot (skyline987650@gmail.com)"   # = off_user_agent в .env
LC = "ru"
PAGE_SIZE = 20
PROD_TIMEOUT_S = 2.5      # off_timeout_s — прод сдаётся здесь
REQUEST_TIMEOUT_S = 15.0  # здесь ждём дольше, чтобы отличить «медленно» от «недоступно»
PAUSE_S = 7.0             # уважаем лимит OFF ~10 запросов/мин
FIELDS = (
    "code,product_name,product_name_ru,brands,quantity,"
    "nutrition_data_per,nutriments,completeness"
)

# (id кейса, бренд, название, жирность %, эталонные ккал/100 г)
# Источник: tests/eval/runs/2026-07-27_gpt-5.4-mini.json + manifest.yaml
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

# Кейсы, где проверки C2–C4 слепы — ради них всё и затевается
BLIND = {'A-05', 'A-13'}


def search_terms(brand, name, fat_pct):
    """Повторяет OpenFoodFactsSource._search_terms — бренд + имя + жирность."""
    fat = f"{fat_pct:g}%" if fat_pct is not None else None
    return " ".join(p for p in (brand, name, fat) if p)


def num(nutriments, key):
    try:
        v = nutriments.get(key)
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def kcal_of(product):
    """Повторяет map_product: ккал напрямую либо из кДж; нужны все 4 значения."""
    n = product.get("nutriments") or {}
    kcal = num(n, "energy-kcal_100g")
    if kcal is None:
        kj = num(n, "energy-kj_100g")
        kcal = round(kj / 4.184, 1) if kj is not None else None
    p, f, c = num(n, "proteins_100g"), num(n, "fat_100g"), num(n, "carbohydrates_100g")
    if None in (kcal, p, f, c):
        return None
    return kcal


def best_name(product):
    return (product.get("product_name_ru") or product.get("product_name") or "").strip()


def query_off(terms):
    """GET к OFF. Возвращает (данные|None, латентность, ошибка|None)."""
    params = {
        "search_terms": terms, "search_simple": "1", "action": "process",
        "json": "1", "page_size": str(PAGE_SIZE), "lc": LC, "fields": FIELDS,
    }
    url = f"{BASE_URL}/cgi/search.pl?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S, context=ctx) as r:
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
    except Exception as e:                       # noqa: BLE001 — диагностика, не прод
        return None, time.monotonic() - t0, f"{type(e).__name__}: {e}"


def main():
    print("=" * 78)
    print("Замер покрытия Open Food Facts — 25 реальных товаров яруса A")
    print(f"База: {BASE_URL}   лимит прода: {PROD_TIMEOUT_S} с   пауза: {PAUSE_S} с")
    print("=" * 78)

    rows, latencies, hard_fail = [], [], 0

    for idx, (cid, brand, name, fat, ref_kcal) in enumerate(CASES, 1):
        terms = search_terms(brand, name, fat)
        mark = " <— слепой кейс" if cid in BLIND else ""
        print(f"\n[{idx:2}/{len(CASES)}] {cid}{mark}\n    запрос: {terms}")

        data, dt, err = query_off(terms)
        latencies.append(dt)

        if err:
            print(f"    ОШИБКА: {err}  ({dt:.1f} с)")
            rows.append((cid, "ОШИБКА", err, dt, None, ref_kcal))
            hard_fail += 1
        else:
            products = data.get("products") or []
            complete = [p for p in products if kcal_of(p) is not None]
            if not products:
                verdict, detail, got = "НЕ НАЙДЕН", "выдача пуста", None
            elif not complete:
                verdict = "БЕЗ КБЖУ"
                detail = f"{len(products)} карточек, ни одной с полным КБЖУ"
                got = None
            else:
                top = complete[0]
                got = kcal_of(top)
                diff = abs(got - ref_kcal) / max(ref_kcal, 1)
                verdict = "СОШЁЛСЯ" if diff <= 0.15 else "РАСХОЖДЕНИЕ"
                detail = (f"«{best_name(top)[:40]}» {got} ккал "
                          f"vs эталон {ref_kcal} (откл. {diff:.0%}); "
                          f"полных карточек {len(complete)}/{len(products)}")
            slow = "  ⚠ дольше прод-лимита" if dt > PROD_TIMEOUT_S else ""
            print(f"    {verdict}: {detail}")
            print(f"    время: {dt:.2f} с{slow}")
            rows.append((cid, verdict, detail, dt, got, ref_kcal))

        if idx < len(CASES):
            time.sleep(PAUSE_S)

    # ── Итоги ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("ИТОГИ")
    print("=" * 78)

    if hard_fail == len(CASES):
        print("\n❌ OFF НЕДОСТУПЕН с этой машины — ни один запрос не прошёл.")
        print("   Это блокер: решение OQ-1 (сверка с внешним источником) неисполнимо,")
        print("   и лестница фолбэков в проде фактически работает как label→estimate.")
        return 1

    def count(v):
        return sum(1 for r in rows if r[1] == v)

    total = len(rows)
    found = count("СОШЁЛСЯ") + count("РАСХОЖДЕНИЕ")
    print(f"\n  всего товаров        : {total}")
    print(f"  найдено с полным КБЖУ: {found}  ({found / total:.0%})")
    print(f"    из них сошлось ≤15% : {count('СОШЁЛСЯ')}")
    print(f"    из них расхождение  : {count('РАСХОЖДЕНИЕ')}")
    print(f"  найдено без КБЖУ     : {count('БЕЗ КБЖУ')}")
    print(f"  не найдено           : {count('НЕ НАЙДЕН')}")
    print(f"  ошибок запроса       : {count('ОШИБКА')}")

    ok_lat = sorted(latencies)
    med = ok_lat[len(ok_lat) // 2]
    slow_n = sum(1 for x in latencies if x > PROD_TIMEOUT_S)
    print(f"\n  латентность: медиана {med:.2f} с, максимум {max(latencies):.2f} с")
    print(f"  не уложились в прод-лимит {PROD_TIMEOUT_S} с: {slow_n} из {total}"
          f"  ({slow_n / total:.0%})")
    if slow_n > total * 0.2:
        print("  ⚠ off_timeout_s=2.5 занижен — в проде эти позиции молча уйдут в estimate")

    print("\n  Слепые кейсы (ради них вводилась сверка, §7 ТЗ):")
    for cid, verdict, detail, dt, got, ref in rows:
        if cid in BLIND:
            print(f"    {cid}: {verdict} — {detail}")

    print("\n  Как читать результат:")
    print("   • покрытие ≥60% и оба слепых кейса найдены → решение OQ-1 работает,")
    print("     веб-ступень R2 можно не торопить;")
    print("   • покрытие <30% или слепые кейсы не найдены → сверка почти ничего не даёт,")
    print("     и R2 (веб-поиск) становится главным механизмом точности, а не расширением;")
    print("   • много «РАСХОЖДЕНИЕ» → сверка будет часто дёргать пользователя ложной")
    print("     тревогой; порог resolver_disagree_pct=15% надо поднимать.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
