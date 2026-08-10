"""scripts/smoke.py — смоук выкатки: несколько фиксированных кейсов через ЖИВОЙ прод.

    python scripts/smoke.py --base-url https://<vps> --user-id <telegram-id>

**Чем это НЕ является — а именно вторым раннером.** `tests/eval/run_eval.py` уже
делает «фото + эталоны + прогон + пороги» на 25 кейсах, и продублировать его тут
незаметно очень легко: снаружи задачи выглядят одинаково. Различие в том, ЧТО
проверяется:

| | `run_eval.py` | смоук |
|---|---|---|
| вопрос | насколько точна модель | жив ли задеплоенный путь |
| куда ходит | `LLMExtractor` напрямую | **HTTP задеплоенного инстанса** |
| чей конфиг | локальный | **прод-`.env` на VPS** |
| порог | kcal ≤2% | коридор ±25% |
| что докажет провал | модель/промпт хуже | **выкатили не то, или среда сломана** |

Раннер проверяет КОД, смоук — СРЕДУ: загрузился ли прод-`.env`, жив ли реальный
ключ, есть ли сеть с VPS до провайдера, применились ли миграции, тот ли процесс
работает. Ни одно из этих свойств локальным прогоном не воспроизводится —
поэтому `--base-url` обязателен и без дефолта: дефолт на `localhost` рано или
поздно даст зелёный смоук против не того процесса, и это хуже его отсутствия.

Эталоны НЕ дублируются: `tests/smoke/cases.yaml` ссылается на ярус A по id, числа
приходят из `tests/eval/manifest.yaml`. Сжатие снимков и чтение эталона берутся
из раннера по той же причине — второй экземпляр правды разъезжается молча.

В дневник смоук не пишет: `/analyze` и `/foods/analyze` не трогают БД (следствие
паттерна analyze→confirm→save). Свойство неочевидное, поэтому оно под тестом
(`tests/test_smoke_cases.py`), а не под этим комментарием.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows-консоль по умолчанию cp1251 и падает на ✓/±/кириллице в рамках.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import aiohttp  # noqa: E402
import yaml  # noqa: E402

from app.config import Settings, settings  # noqa: E402
from app.contexts.nutrition.resolve.checks import (  # noqa: E402
    FLAG_ARITH_MISMATCH,
    FLAG_BOUNDS_FAIL,
    FLAG_KJ_KCAL_CONFLICT,
)
from app.delivery.miniapp_api.health import (  # noqa: E402
    HEALTH_FIELDS,
    HEALTH_PATH,
    SHA_LEN,
    read_commit,
)
from app.kernel.auth import make_init_data  # noqa: E402

# Из раннера: разбор манифеста, приведение эталона к 100 г и КЛИЕНТСКОЕ сжатие
# (1280 px / q82 — ровно то, что делает фронт в api.js). Сжатие здесь не
# косметика: без него 11 копий кадра для S-05 дают ~44 MB base64 и упираются в
# `client_max_size`, то есть негативный контроль вернул бы 413 транспорта вместо
# проверяемого отказа по капу.
from tests.eval.run_eval import (  # noqa: E402
    EVAL_DIR,
    gt_per100g,
    load_all,
    prep_image,
)

SMOKE_DIR = ROOT / "tests" / "smoke"
CASES_FILE = SMOKE_DIR / "cases.yaml"
# Артефакты — СВОЙ каталог. Не `tests/eval/runs/` и не `tests/eval/probes/`: общий
# каталог у инструментов с разными контрактами дважды ронял сводку раннера
# (хвост №4 блока 11), оба раза чинили симптом.
RUNS_DIR = SMOKE_DIR / "runs"

# ── Пороги и константы проверок ──────────────────────────────────────────────

# Коридор НЕ калиброван под данные — он выведен из двух измеренных величин и
# лежит в разрыве между ними:
#   снизу — разброс между прогонами ОДНОГО промпта уже измерен: A-13 гулял
#           8.5% ↔ 14.3% при temperature=0.2. Порог ниже ~15% будет мигать, а
#           мигающий смоук перестают читать, то есть он хуже отсутствующего;
#   сверху — F-01 (кДж записаны в ккал) — это ошибка в 4.18 раза, то есть 318%.
#           Её ловит любой коридор вплоть до ±50%.
KCAL_CORRIDOR = 0.25

# Отказ по капу — 422, а НЕ 400. В ТЗ задачи (§3.3) написано «400»; это
# расхождение с реальностью, а не требование: кап поднимает
# `InvalidInput` из contexts.nutrition, а общий error-middleware миниапы мапит
# его в 422 `invalid_input`, и ровно 422 задокументирован в `openapi.yaml`
# («превышение — 422, не обрезка»). Смоук обязан ждать то, что система
# гарантирует; ожидание 400 сделало бы кейс вечно красным.
CAP_REJECT_STATUS = 422
CAP_REJECT_CODE = "invalid_input"

# Провенансы, законные на кадре БЕЗ штрихкода. `off` в списке есть — но только
# как lookup по коду (см. `_off_is_barcode_lookup`): вариант D удалил поиск по
# названию, и появление `off` без кода означает, что выкачено не то.
ALLOWED_SOURCES = frozenset({"label", "estimate", "off"})
_OFF_BY_CODE = re.compile(r"/product/\d+/?$")

# Флаги, которых на исправной этикетке быть не должно. Коды импортируются из
# кода, а не переписываются строками: переименуют — тест упадёт, а не разойдётся.
FORBIDDEN_WARNINGS = (FLAG_KJ_KCAL_CONFLICT, FLAG_ARITH_MISMATCH)
# C4.1 — физически невозможные числа. Единственная числовая проверка текстового
# кейса: эталона у «200 г гречки» нет, а границы не требуют эталона.
BOUNDS_WARNINGS = (FLAG_BOUNDS_FAIL,)

ANALYZE_PATH = "/api/v1/nutrition/analyze"
FOODS_ANALYZE_PATH = "/api/v1/nutrition/foods/analyze"

# Белый список зашит в КОДЕ, а не берётся параметром: параметр однажды передадут
# неверно, и смоук напишет в дневник двух живых людей. `/foods/analyze` в списке
# разрешён (следующий естественный кейс), но текущим набором не вызывается.
ALLOWED_PATHS = frozenset({HEALTH_PATH, ANALYZE_PATH, FOODS_ANALYZE_PATH})


class Failure(Exception):
    """Кейс не прошёл — но прогон продолжается: важен полный знаменатель."""


# ── Чистые проверки (тестируются без сети) ───────────────────────────────────

def check_health(payload, *, expect_commit: str, expect_model: str) -> list[str]:
    """`/health` — «выкатили новый код», а не «перезапустили старый»."""
    if not isinstance(payload, dict):
        return [f"тело не объект: {payload!r}"]
    errs = []
    missing = [f for f in HEALTH_FIELDS if f not in payload]
    if missing:
        errs.append(f"в ответе нет полей: {missing}")
    got_commit = str(payload.get("commit", ""))
    want = expect_commit[:SHA_LEN]
    if got_commit != want:
        errs.append(
            f"коммит прода {got_commit!r} != ожидаемого {want!r} — "
            "процесс работает не на том коде, который проверяют"
        )
    if payload.get("model") != expect_model:
        errs.append(f"модель прода {payload.get('model')!r} != {expect_model!r}")
    return errs


def _first_item(body) -> dict | None:
    items = (body or {}).get("items") or []
    return items[0] if items else None


def _off_is_barcode_lookup(item: dict) -> bool:
    """`off` законен только как точный lookup по штрихкоду (ADR-0019, вариант D).

    Поисковая ветка по названию удалена 2026-08-03: она подменяла продукт в 11
    случаях из 25. Признак того, что ступень отработала по коду, — `source_ref`
    вида `.../product/<цифры>`; `off` без такого ref означает, что выкачено не то.
    """
    return bool(_OFF_BY_CODE.search(str(item.get("source_ref") or "")))


def check_analyze_shape(body) -> list[str]:
    """Структурные проверки, общие для всех кейсов /analyze.

    Они детерминированы и потому ценнее числовой: не зависят ни от температуры
    модели, ни от того, какой товар в кадре.
    """
    if not isinstance(body, dict):
        return [f"тело не объект: {body!r}"]
    errs = []
    items = body.get("items")
    if not isinstance(items, list) or not items:
        errs.append("items пуст — разбор не дал ни одной позиции")
    unresolved = body.get("unresolved")
    if unresolved:
        errs.append(
            f"unresolved непуст ({len(unresolved)}): ветка отказа сработала там, "
            "где не должна"
        )
    if not isinstance(items, list):
        return errs
    for i, item in enumerate(items):
        source = item.get("source")
        if source not in ALLOWED_SOURCES:
            errs.append(f"items[{i}].source={source!r} вне {sorted(ALLOWED_SOURCES)}")
        elif source == "off" and not _off_is_barcode_lookup(item):
            errs.append(
                f"items[{i}]: source=off без штрихкода в source_ref "
                f"({item.get('source_ref')!r}) — поисковая ветка OFF вернулась"
            )
        per100g = item.get("per100g")
        if not isinstance(per100g, dict) or per100g.get("kcal") is None:
            # Без per100g экран подтверждения не пересчитает КБЖУ при правке веса.
            errs.append(f"items[{i}]: нет per100g — confirm-экран не пересчитает вес")
    return errs


def check_warnings(body, forbidden) -> list[str]:
    errs = []
    for i, item in enumerate((body or {}).get("items") or []):
        hit = sorted(set(item.get("warnings") or []) & set(forbidden))
        if hit:
            errs.append(f"items[{i}]: сработали проверки {hit}")
    return errs


def check_kcal_corridor(got, expected, corridor: float = KCAL_CORRIDOR) -> list[str]:
    """Единственная числовая проверка: per100g.kcal против эталона яруса A."""
    if expected in (None, 0):
        return ["эталонных ккал нет — сравнивать не с чем"]
    if got is None:
        return ["в ответе нет per100g.kcal"]
    dev = abs(float(got) - float(expected)) / abs(float(expected))
    if dev > corridor:
        return [
            f"ккал {float(got):g} против эталона {float(expected):g} — "
            f"отклонение {dev * 100:.0f}% > {corridor * 100:.0f}%"
        ]
    return []


def check_photo_case(body, expected_kcal) -> list[str]:
    errs = check_analyze_shape(body)
    errs += check_warnings(body, FORBIDDEN_WARNINGS)
    item = _first_item(body)
    if item is None:
        return errs
    errs += check_kcal_corridor((item.get("per100g") or {}).get("kcal"), expected_kcal)
    return errs


def check_text_case(body) -> list[str]:
    """Текстовая ветка: структура + границы C4.1, числового порога нет (§3.3)."""
    return check_analyze_shape(body) + check_warnings(body, BOUNDS_WARNINGS)


def check_cap_reject(status: int, body, cap: int) -> list[str]:
    """Кап блока 15 жив: превышение — ОТКАЗ, а не молчаливая обрезка."""
    errs = []
    if status != CAP_REJECT_STATUS:
        errs.append(
            f"статус {status} вместо {CAP_REJECT_STATUS}"
            + (
                " — сервис принял ввод сверх капа: вернулась молчаливая обрезка"
                if status == 200
                else ""
            )
        )
    if isinstance(body, dict):
        code, message = body.get("code"), str(body.get("message") or "")
        if status == CAP_REJECT_STATUS and code != CAP_REJECT_CODE:
            errs.append(f"код ошибки {code!r} вместо {CAP_REJECT_CODE!r}")
        if status == CAP_REJECT_STATUS and str(cap) not in message:
            errs.append(f"в тексте отказа не назван предел {cap}: {message!r}")
    return errs


# ── Транспорт ────────────────────────────────────────────────────────────────

class Client:
    """HTTP по белому списку путей. Сетевую ошибку отдаёт как результат, не как
    исключение: кейс обязан ЗАВАЛИТЬСЯ, а не исчезнуть из знаменателя."""

    def __init__(self, session, base_url: str, init_data: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.init_data = init_data

    async def _call(self, method: str, path: str, *, payload=None, auth=True):
        if path not in ALLOWED_PATHS:
            raise RuntimeError(f"путь {path} не в белом списке — вызов запрещён")
        headers = {"X-Telegram-Init-Data": self.init_data} if auth else {}
        t0 = time.monotonic()
        try:
            async with self.session.request(
                method, f"{self.base_url}{path}", json=payload, headers=headers
            ) as resp:
                text = await resp.text()
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    body = {"_raw": text[:400]}
                return resp.status, body, round(time.monotonic() - t0, 2)
        except Exception as exc:  # noqa: BLE001 — сеть/таймаут/DNS = провал кейса
            return 0, {"_error": f"{type(exc).__name__}: {exc}"}, round(
                time.monotonic() - t0, 2
            )

    async def get(self, path: str, *, auth: bool = True):
        return await self._call("GET", path, auth=auth)

    async def post(self, path: str, payload: dict, *, auth: bool = True):
        return await self._call("POST", path, payload=payload, auth=auth)


def _transport_error(body) -> str | None:
    if isinstance(body, dict) and body.get("_error"):
        return str(body["_error"])
    return None


def _data_url(raw: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


# ── Кейсы ────────────────────────────────────────────────────────────────────

def load_smoke_cases(path: Path = CASES_FILE) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("cases") or [])


def a_index() -> dict:
    return {c["id"]: c for c in load_all() if c.get("tier") == "A"}


def resolve_case(case: dict, index: dict) -> tuple[Path | None, float | None]:
    """Снимок и эталонные ккал/100 г из яруса A. Копий эталона тут нет."""
    ref = index.get(case.get("case"))
    if ref is None:
        raise Failure(f"кейс {case.get('case')!r} отсутствует в manifest.yaml")
    image = EVAL_DIR / ref["image"] if ref.get("image") else None
    if image is None or not image.exists():
        raise Failure(f"нет снимка для {ref['id']}: {ref.get('image')}")
    return image, gt_per100g(ref["ground_truth"])["kcal"]


async def run_health(client: Client, *, expect_commit: str, expect_model: str) -> dict:
    status, body, latency = await client.get(HEALTH_PATH, auth=False)
    if err := _transport_error(body):
        errs = [f"недоступен: {err}"]
    elif status != 200:
        errs = [f"статус {status}, тело {body!r}"]
    else:
        errs = check_health(body, expect_commit=expect_commit, expect_model=expect_model)
    return {"id": "HEALTH", "kind": "health", "status": status,
            "latency_s": latency, "body": body, "errors": errs}


async def run_case(client: Client, case: dict, index: dict, cap: int) -> dict:
    cid, kind = case.get("id", "?"), case.get("kind")
    out = {"id": cid, "kind": kind, "status": None, "latency_s": None,
           "body": None, "errors": []}
    try:
        if kind == "text":
            payload = {"type": "text", "text": case["text"]}
        elif kind in ("photo", "cap_reject"):
            image, expected = resolve_case(case, index)
            data_url = _data_url(prep_image(image))
            repeat = int(case.get("repeat", 1)) if kind == "cap_reject" else 1
            payload = {"type": "photo", "images": [data_url] * repeat}
            out["expected_kcal"] = expected
        else:
            raise Failure(f"неизвестный kind={kind!r}")
    except Failure as exc:
        out["errors"] = [str(exc)]
        return out

    status, body, latency = await client.post(ANALYZE_PATH, payload)
    out.update(status=status, latency_s=latency, body=_trim(body))

    if err := _transport_error(body):
        out["errors"] = [f"запрос не прошёл: {err}"]
        return out

    if kind == "cap_reject":
        out["errors"] = check_cap_reject(status, body, cap)
        return out
    if status != 200:
        out["errors"] = [f"статус {status}, тело {body!r}"]
        return out
    out["errors"] = (
        check_text_case(body) if kind == "text"
        else check_photo_case(body, out.get("expected_kcal"))
    )
    return out


def _trim(body):
    """В артефакт кладём ответ без сырых картинок — их там и нет, но тело режем
    по длине: артефакт должен читаться глазами."""
    text = json.dumps(body, ensure_ascii=False)
    return body if len(text) <= 20000 else {"_truncated": text[:20000]}


# ── Прогон ───────────────────────────────────────────────────────────────────

def resolve_token() -> tuple[str, str]:
    """Токен и ИМЯ источника. В аргументах командной строки секрета быть не должно
    — он уедет в history шелла; печатаем только имя, никогда значение."""
    for name in ("SMOKE_BOT_TOKEN", "BOT_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value, name
    if settings.bot_token is not None:
        raw = settings.bot_token.get_secret_value()
        if raw:
            return raw, "локальный .env (settings.bot_token)"
    raise SystemExit(
        "Нет токена бота: задай SMOKE_BOT_TOKEN (или BOT_TOKEN) в окружении.\n"
        "Токен нужен для подписи initData — прод проверяет её тем же алгоритмом."
    )


async def run(args) -> int:
    cases = load_smoke_cases(Path(args.cases))
    if not cases:
        print("FAIL: в cases.yaml нет ни одного кейса — проверять нечего")
        return 1

    token, token_src = resolve_token()
    init_data = make_init_data(token, args.user_id)
    index = a_index()

    print(f"смоук против {args.base_url}")
    print(f"  пользователь {args.user_id}, токен из: {token_src}")
    print(f"  ждём коммит {args.expect_commit[:SHA_LEN]}, модель {args.expect_model}")
    print(f"  кейсов: {len(cases)} + health\n")

    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        client = Client(session, args.base_url, init_data)
        results = [await run_health(
            client, expect_commit=args.expect_commit, expect_model=args.expect_model
        )]
        for case in cases:
            results.append(await run_case(client, case, index, args.cap))

    # Знаменатель обязан сойтись. Прогон, в котором «0 проверено, всё ок», — это
    # ровно тот дефект, что дал ложное «0 кДж-утечек у всех» при ретропрогоне
    # 02.08: отрицательный результат обязан предъявлять размер знаменателя.
    expected_n = len(cases) + 1
    if len(results) != expected_n:
        print(f"FAIL: проверено {len(results)} из {expected_n} — прогон неполный")
        return 1

    for r in results:
        mark = "ok  " if not r["errors"] else "FAIL"
        latency = "" if r["latency_s"] is None else f" {r['latency_s']}s"
        print(f"[{mark}] {r['id']:<7} {r['kind']:<10} http={r['status']}{latency}")
        for err in r["errors"]:
            print(f"         └─ {err}")

    for hint in diagnose(results):
        print(f"\n  ⟶ {hint}")

    failed = [r["id"] for r in results if r["errors"]]
    artifact = write_artifact(args, results)
    print(f"\nартефакт: {_display(artifact)}")
    if failed:
        print(f"ИТОГ: провалено {len(failed)} из {expected_n} — {', '.join(failed)}")
        return 1
    print(f"ИТОГ: {expected_n} из {expected_n} — выкатка проверена")
    return 0


def diagnose(results: list[dict]) -> list[str]:
    """Подсказка по КАРТИНЕ отказов, а не по отдельному кейсу.

    Ради этого `/health` и стоит первым (§1 задания): «выкачен не тот код» и «auth
    не принял подпись» — два разных отказа, и различать их обязан инструмент, а не
    человек глазами по шести одинаковым строкам. Первый прогон против прода
    (2026-08-10) дал ровно такую картину — живой `/health` с верным коммитом плюс
    401 на всех пяти кейсах, — и вывод «дело в токене, а не в выкатке» приходилось
    делать вручную.

    Подсказки ничего не решают и не влияют на код возврата: они только называют
    самую вероятную причину. Молчание здесь — тоже ответ (картина не типовая).
    """
    cases = [r for r in results if r["kind"] != "health"]
    health = next((r for r in results if r["kind"] == "health"), None)
    if not cases:
        return []
    hints: list[str] = []
    every = lambda code: all(r["status"] == code for r in cases)  # noqa: E731

    if every(401):
        hints.append(
            "все кейсы 401 при живом /health: код на проде ТОТ (коммит сошёлся), а подпись "
            "initData не принята — расходится токен бота, а не выкатка. "
            "Прогон подписывает прод-токеном из SMOKE_BOT_TOKEN."
        )
    if every(403):
        hints.append(
            "все кейсы 403: --user-id не в whitelist прода (ALLOWED_USER_IDS в прод-.env)."
        )
    if every(0):
        hints.append(
            "ни один запрос не дошёл: проверь --base-url (у quick-туннеля "
            "*.trycloudflare.com имя меняется при каждом рестарте) и что сервис поднят."
        )
    if health and health["errors"] and all(not r["errors"] for r in cases):
        hints.append(
            "кейсы проходят, а /health нет: похоже, выкачен код без эндпоинта версии — "
            "то есть ровно тот случай, ради которого он и заводился."
        )
    return hints


def _display(path: Path) -> str:
    """Путь относительно репо, когда это возможно. Тесты подменяют `RUNS_DIR` на
    tmp_path — он вне репо, и жёсткий `relative_to` там просто падал."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_artifact(args, results) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{date.today().isoformat()}_smoke.json"
    out.write_text(
        json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "base_url": args.base_url,
                "user_id": args.user_id,
                "expect_commit": args.expect_commit,
                "expect_model": args.expect_model,
                "corridor": KCAL_CORRIDOR,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Смоук выкатки против живого инстанса")
    # Обязательный и БЕЗ дефолта — осознанно (см. шапку модуля).
    p.add_argument("--base-url", required=True, help="https://<vps>, без пути")
    p.add_argument("--user-id", type=int, required=True,
                   help="telegram id из whitelist прода (иначе 403)")
    p.add_argument("--expect-commit", default=read_commit(ROOT),
                   help="ожидаемый SHA; по умолчанию локальный HEAD")
    # Дефолт — значение из КОДА, а не из локального `.env`: вопрос смоука «выкачен
    # ли текущий код», и локальный оверрайд на него ответа не даёт.
    p.add_argument("--expect-model",
                   default=Settings.model_fields["llm_vision_model"].default,
                   help="ожидаемая vision-модель на проде")
    p.add_argument("--cap", type=int,
                   default=Settings.model_fields["api_max_images"].default,
                   help="ожидаемый кап числа фото (для текста отказа)")
    p.add_argument("--cases", default=str(CASES_FILE))
    p.add_argument("--timeout", type=float, default=120.0,
                   help="таймаут одного HTTP-запроса, с")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
