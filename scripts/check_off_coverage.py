#!/usr/bin/env python3
"""check_off_coverage.py — диагностика ступени OFF с прод-машины (v3).

ИСТОРИЯ ЗАМЕРА (важна, чтобы не повторить ошибки):
  v1 — ходил в `/cgi/search.pl`: 18 из 25 запросов → HTTP 503 за 0.06 с. Причина не
       в лимитах: легаси-бэкенд поиска OFF (Perl), питающий и `/cgi/search.pl`, и
       `/api/v2/search`, устарел и отдаёт 503 массово. Замер недействителен.
  v2 — перешёл на search-a-licious: 200 OK, 25/25 «найдено». Тоже недействителен:
       скрипт брал первую карточку с полным КБЖУ, минуя `select_best()` из прода.
       Elasticsearch всегда возвращает топ-20 по релевантности, поэтому «найдено»
       означало «что-то вернулось», а не «нашёлся нужный товар». На запрос
       «сыр плавленый фетакса» возвращался просто «Сыр плавленый», на «онигири с
       запечённым тунцом» — «Онигири с креветкой».

ЧТО ДЕЛАЕТ v3:
  1. Переносит `select_best()` из прода ДОСЛОВНО (бренд ×3, доля токенов имени ×2,
     близость жирности +1, completeness) и печатает СКОР победителя.
  2. Отдельно ищет среди всех 20 кандидатов те, чьи ккал сходятся с эталоном ≤10%,
     и печатает их ранг. Это разделяет два разных диагноза:
        «товара нет в базе»  ≠  «товар есть, но ранжирован не первым».
  3. Подбирает минимальный порог скора, ниже которого матч надо отбрасывать.
     В проде такого порога НЕТ — `select_best` возвращает топ-1 всегда.

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

USER_AGENT = "lifeManager_bot/1.0 (skyline987650@gmail.com)"
REQUEST_TIMEOUT_S = 15.0
PROD_TIMEOUT_S = 2.5
PAUSE_S = 5.0
PAGE_SIZE = 20
KCAL_MATCH_TOL = 0.10     # «это тот самый товар» — ккал сходятся в пределах 10%

SEARCH_URL = "https://search.openfoodfacts.org/search"
BARCODE_URL = "https://world.openfoodfacts.org/api/v2/product/{}"
PROBE_BARCODE = "3017620422003"   # Nutella — эталон живости

# (id, бренд, название, жирность %, эталонные ккал/100 г)
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
BLIND = {'A-05', 'A-13'}


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


# ── Прод-логика, перенесённая ДОСЛОВНО из openfoodfacts.py ──────────────────
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
    return [t for t in _norm(v).replace(",", " ").split() if t]


def _first_brand(brands):
    """ВАЖНО: в легаси `brands` — CSV-строка, в search-a-licious — СПИСОК или её нет.
    Прод (`_first_brand`) умеет только строку и падает на списке — дефект найден
    этим замером."""
    if not brands:
        return ""
    if isinstance(brands, (list, tuple)):
        return str(brands[0]).strip() if brands else ""
    return str(brands).split(",")[0].strip()


def _best_name(p):
    """ВАЖНО: `product_name_ru` в search-a-licious НЕ существует — только
    `product_name` (на языке карточки) и `product_name_en`. Прод рассчитывает
    на `product_name_ru` как на основной источник."""
    for k in ("product_name_ru", "product_name", "product_name_en"):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def map_product(p):
    """Нет любого из 4 значений → None (карточка отбрасывается)."""
    n = p.get("nutriments") or {}
    kcal = _num(n, "energy-kcal_100g")
    if kcal is None:
        kj = _num(n, "energy-kj_100g")
        kcal = round(kj / 4.184, 1) if kj is not None else None
    pr, fa, ca = _num(n, "proteins_100g"), _num(n, "fat_100g"), _num(n, "carbohydrates_100g")
    return None if None in (kcal, pr, fa, ca) else kcal


def score_of(product, brand, name, fat_pct):
    """Дословный перенос score() из select_best (§7.2)."""
    s = 0.0
    brand_norm = _norm(brand)
    name_tokens = set(_tokens(name))
    prod_brand = _norm(_first_brand(product.get("brands")))
    prod_name = _norm(_best_name(product))
    if brand_norm and (brand_norm in prod_brand or brand_norm in prod_name):
        s += 3.0
    if name_tokens:
        prod_tokens = set(_tokens(_best_name(product)))
        s += 2.0 * (len(name_tokens & prod_tokens) / len(name_tokens))
    if fat_pct is not None:
        pf = _num(product.get("nutriments") or {}, "fat_100g")
        if pf is not None and abs(pf - fat_pct) < 0.6:
            s += 1.0
    try:
        s += float(product.get("completeness") or 0.0)
    except (TypeError, ValueError):
        pass
    return s


def select_best(products, brand, name, fat_pct):
    """ВНИМАНИЕ: как в проде — минимального порога скора НЕТ, топ-1 всегда."""
    cands = [p for p in products if map_product(p) is not None]
    if not cands:
        return None, 0.0
    best = max(cands, key=lambda p: score_of(p, brand, name, fat_pct))
    return best, score_of(best, brand, name, fat_pct)


def terms_of(brand, name, fat_pct):
    fat = f"{fat_pct:g}%" if fat_pct is not None else None
    return " ".join(p for p in (brand, name, fat) if p)


# ── ЭТАП 1. Разведка ────────────────────────────────────────────────────────
def probe():
    print("=" * 78)
    print("ЭТАП 1 — РАЗВЕДКА")
    print("=" * 78)

    print("\n  search-a-licious — форма ответа (нужна для переписывания адаптера):")
    data, dt, err = search("кефир 3.2%")
    if err:
        print(f"    ✗ {err} ({dt:.2f} с) — дальше идти некуда")
        return False, False
    hits = data.get("hits") or []
    print(f"    ✓ 200 OK, {len(hits)} карточек ({dt:.2f} с)")
    if hits:
        h = hits[0]
        print(f"    ВСЕ ключи карточки ({len(h)}):")
        for i in range(0, len(sorted(h.keys())), 6):
            print("      " + ", ".join(sorted(h.keys())[i:i + 6]))
        has = [k for k in ("product_name", "product_name_ru", "brands",
                           "completeness", "nutriments", "code", "_score")
               if k in h]
        miss = [k for k in ("product_name", "product_name_ru", "brands",
                            "completeness", "nutriments", "code") if k not in h]
        print(f"    поля, нужные проду — есть: {has}")
        print(f"    поля, нужные проду — НЕТ : {miss if miss else '— все на месте'}")
        n = h.get("nutriments") or {}
        print(f"    ключи nutriments (первые 10): {sorted(n.keys())[:10]}")
        print(f"    map_product даёт: {map_product(h)}")

        # Перепись сигналов матчинга по ВСЕЙ выдаче — какие поля реально выживают
        print(f"\n    Наличие полей в {len(hits)} карточках выдачи:")
        for key in ("product_name", "product_name_ru", "product_name_en",
                    "brands", "completeness", "code", "_score"):
            have = sum(1 for x in hits if x.get(key) not in (None, "", [], {}))
            types = {type(x.get(key)).__name__ for x in hits if x.get(key) is not None}
            t = "/".join(sorted(types)) if types else "—"
            flag = "" if have == len(hits) else "   ⚠ не у всех"
            print(f"      {key:18} {have:>2}/{len(hits)}  тип: {t}{flag}")
        full = sum(1 for x in hits if map_product(x) is not None)
        print(f"      {'полное КБЖУ':18} {full:>2}/{len(hits)}")
        cyr = sum(1 for x in hits
                  if any('а' <= c.lower() <= 'я' for c in _best_name(x)))
        print(f"      {'имя кириллицей':18} {cyr:>2}/{len(hits)}"
              f"   ← без product_name_ru это единственный источник рус. названия")

    time.sleep(3)
    print("\n  barcode /api/v2/product — отдельный путь:")
    data, dt, err = http_get(BARCODE_URL.format(PROBE_BARCODE)
                             + "?fields=code,product_name,brands,nutriments")
    if err:
        print(f"    ✗ {err} ({dt:.2f} с)")
        return True, False
    prod = data.get("product") or {}
    print(f"    ✓ «{_best_name(prod)[:40]}» → {map_product(prod)} ккал ({dt:.2f} с)")
    return True, True


# ── ЭТАП 2. Матчинг ─────────────────────────────────────────────────────────
def measure():
    print("\n" + "=" * 78)
    print("ЭТАП 2 — МАТЧИНГ с прод-логикой select_best")
    print("=" * 78)

    rows, lats = [], []
    for idx, (cid, brand, name, fat, ref) in enumerate(CASES, 1):
        terms = terms_of(brand, name, fat)
        mark = "  <— слепой кейс" if cid in BLIND else ""
        print(f"\n[{idx:2}/{len(CASES)}] {cid}{mark}   запрос: {terms}")

        data, dt, err = search(terms)
        lats.append(dt)
        if err:
            print(f"    ОШИБКА: {err} ({dt:.2f} с)")
            rows.append((cid, "ОШИБКА", 0.0, None, None, ref))
            continue

        hits = data.get("hits") or []
        cands = [h for h in hits if map_product(h) is not None]
        best, sc = select_best(hits, brand, name, fat)

        # есть ли ВООБЩЕ в выдаче карточка с сошедшимися ккал и на каком ранге
        right_rank, right_name = None, None
        for r, h in enumerate(cands, 1):
            k = map_product(h)
            if abs(k - ref) / max(ref, 1) <= KCAL_MATCH_TOL:
                right_rank, right_name = r, _best_name(h)
                break

        if best is None:
            print(f"    НЕТ КАНДИДАТОВ: {len(hits)} карточек, ни одной с полным КБЖУ")
            rows.append((cid, "НЕТ КБЖУ", 0.0, None, right_rank, ref))
        else:
            got = map_product(best)
            dev = abs(got - ref) / max(ref, 1)
            verdict = "СОШЁЛСЯ" if dev <= 0.15 else "НЕ ТОТ ТОВАР"
            print(f"    выбран: «{_best_name(best)[:42]}» "
                  f"[{_first_brand(best.get('brands'))[:18]}]")
            print(f"    скор={sc:.2f}  ккал={got} vs эталон={ref} (откл. {dev:.0%})"
                  f"  → {verdict}")
            if right_rank:
                print(f"    ✓ подходящая карточка ЕСТЬ в выдаче: ранг {right_rank} "
                      f"«{right_name[:38]}»")
            else:
                print(f"    ✗ среди {len(cands)} карточек с КБЖУ подходящей НЕТ "
                      f"(±{KCAL_MATCH_TOL:.0%})")
            print(f"    время: {dt:.2f} с")
            rows.append((cid, verdict, sc, dev, right_rank, ref))

        if idx < len(CASES):
            time.sleep(PAUSE_S)
    return rows, lats


# ── ЭТАП 3. Сводка и подбор порога ──────────────────────────────────────────
def report(rows, lats, barcode_ok):
    print("\n" + "=" * 78)
    print("ИТОГИ")
    print("=" * 78)
    n = len(rows)
    cnt = lambda v: sum(1 for r in rows if r[1] == v)   # noqa: E731

    print(f"\n  всего                : {n}")
    print(f"  ошибок запроса       : {cnt('ОШИБКА')}")
    print(f"  выбран верный товар  : {cnt('СОШЁЛСЯ')}   ← реальное покрытие")
    print(f"  выбран НЕ ТОТ товар  : {cnt('НЕ ТОТ ТОВАР')}   ← опаснее, чем «не найдено»")
    print(f"  ни одной карточки с КБЖУ: {cnt('НЕТ КБЖУ')}")

    present = sum(1 for r in rows if r[4])
    top1 = sum(1 for r in rows if r[4] == 1)
    print(f"\n  подходящая карточка есть в выдаче: {present}/{n}"
          f"   ← сколько товаров ВООБЩЕ в базе")
    print(f"    из них ранжирована первой       : {top1}")
    print(f"    из них прод-логика взяла не ту  : {present - cnt('СОШЁЛСЯ')}"
          f"   ← чинится матчингом, не источником")

    print("\n  ПОДБОР ПОРОГА СКОРА (в проде порога нет — берётся топ-1 всегда):")
    print(f"  {'порог':>7} | {'останется матчей':>17} | {'из них верных':>14} | {'точность':>9}")
    good = [r for r in rows if r[1] in ("СОШЁЛСЯ", "НЕ ТОТ ТОВАР")]
    for t in (0.0, 1.0, 2.0, 3.0, 3.5, 4.0, 4.5, 5.0):
        kept = [r for r in good if r[2] >= t]
        ok = [r for r in kept if r[1] == "СОШЁЛСЯ"]
        acc = f"{len(ok) / len(kept):.0%}" if kept else "—"
        print(f"  {t:>7.1f} | {len(kept):>17} | {len(ok):>14} | {acc:>9}")

    s = sorted(lats)
    print(f"\n  латентность: медиана {s[len(s) // 2]:.2f} с, максимум {max(s):.2f} с")
    print(f"  не уложились в прод-лимит {PROD_TIMEOUT_S} с: "
          f"{sum(1 for x in lats if x > PROD_TIMEOUT_S)}/{n}")
    print(f"  barcode-путь: {'РАБОТАЕТ' if barcode_ok else 'НЕ РАБОТАЕТ'}")

    print("\n  Слепые кейсы (ради них вводилась сверка, §7 ТЗ):")
    for cid, verdict, sc, dev, rank, ref in rows:
        if cid in BLIND:
            where = f"есть на ранге {rank}" if rank else "в базе НЕ найдена"
            d = f"откл. {dev:.0%}" if dev is not None else "—"
            print(f"    {cid}: {verdict}, скор={sc:.2f}, {d}; подходящая карточка {where}")

    print("\n  Как читать:")
    print("   • «выбран НЕ ТОТ товар» — главное число. Такие матчи в проде получат")
    print("     source=off и уйдут в сверку label-preferred, породив ложную тревогу")
    print("     на экране подтверждения. Это хуже, чем честное «не найдено».")
    print("   • если «подходящая есть в выдаче» >> «выбран верный» — проблема в")
    print("     ранжировании/матчинге, а не в покрытии базы: чинится порогом и")
    print("     скорингом, менять источник не надо.")
    print("   • строка порога, где точность впервые ≥90%, — кандидат в min_score")
    print("     для select_best.")
    print()


def main():
    search_ok, barcode_ok = probe()
    if not search_ok:
        print("\n❌ search-a-licious недоступен — замер невозможен.")
        return 1
    rows, lats = measure()
    report(rows, lats, barcode_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
