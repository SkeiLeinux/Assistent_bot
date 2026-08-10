"""Смоук выкатки: целостность набора кейсов и негативные контроли (DoD п.2–6).

Главный тест файла — не «смоук проходит», а **«смоук можно заставить упасть»**.
Правило проекта (`llm-eval-dataset.md` §3D.1): проверка без негативного контроля
не считается введённой; смоук, который нельзя завалить, ничем не отличается от
его отсутствия — и именно в таком виде он обычно и живёт годами.

Отсюда четыре мутанта против одного положительного контроля:

  - **мёртвый порт** → ВСЕ кейсы FAIL. Не «0 проверено, всё ок»: пустой
    знаменатель, выглядящий успехом, — тот самый дефект, что дал ложное «0
    кДж-утечек у всех» при ретропрогоне 02.08;
  - **подставной эталон A-15** (`kcal: 450`, имитация кДж-утечки) → коридор ±25%
    обязан сработать;
  - **сервер принял 11 снимков** (эквивалент `api_max_images=100` на проде) →
    S-05 обязан упасть: вернулась молчаливая обрезка;
  - **`off` без штрихкода в `source_ref`** → поисковая ветка OFF вернулась.

Положительный контроль обязателен рядом с ними: смоук, который валится всегда,
так же бесполезен, как смоук, который зеленеет всегда, — и мутанты на нём ничего
не доказывают.

Заглушка проверяет `initData` НАСТОЯЩИМ валидатором (`kernel.auth`), поэтому
попутно доказывается, что подпись смоука прод примет.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import scripts.smoke as smoke
from app.config import settings
from app.contexts.nutrition.api import routes as nutri_routes
from app.contexts.nutrition.services import InvalidInput
from app.delivery.miniapp_api.health import HEALTH_PATH, health_payload
from app.kernel.auth import InitDataError, validate_init_data

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "SMOKETESTTOKEN"


# ════════════════════ DoD п.3: кейсы ссылаются на живой ярус A ════════════════
# Эталоны в `cases.yaml` НЕ дублируются — там только id. Проверяем, что ссылка
# ведёт в пригодную запись: ручная копия эталонов в инструмент уже дала
# рассинхрон на 11 кейсах (`llm-eval-dataset.md` §7a), а протухшая ссылка даст
# то же самое молча — смоук будет сверять ответ с эталоном другого продукта.

MACRO_BASIS = "per100g"
VALID_KINDS = {"photo", "text", "cap_reject"}


def check_smoke_cases(cases: list[dict], index: dict) -> list[str]:
    """Нарушения набора кейсов. Возвращает список, не бросает."""
    errs: list[str] = []
    seen: set[str] = set()
    for case in cases:
        cid = case.get("id")
        if cid in seen:
            errs.append(f"{cid}: дубль id")
        seen.add(cid)

        kind = case.get("kind")
        if kind not in VALID_KINDS:
            errs.append(f"{cid}: kind {kind!r} вне {sorted(VALID_KINDS)}")
        if not (case.get("why") or "").strip():
            # Без «зачем» набор через полгода нечем сокращать и нечем защищать.
            errs.append(f"{cid}: не записано why")

        if kind == "text":
            if not (case.get("text") or "").strip():
                errs.append(f"{cid}: текстовый кейс без text")
            if case.get("case"):
                errs.append(f"{cid}: у текстового кейса не может быть ссылки на ярус A")
            continue

        ref = index.get(case.get("case"))
        if ref is None:
            errs.append(f"{cid}: кейс {case.get('case')!r} отсутствует в manifest.yaml")
            continue
        if ref.get("status"):
            errs.append(
                f"{cid}: {ref['id']} имеет status={ref['status']} — "
                "снимка либо нет, либо эталон получен не с него"
            )
        gt = ref.get("ground_truth") or {}
        if gt.get("basis") != MACRO_BASIS:
            errs.append(f"{cid}: {ref['id']} basis={gt.get('basis')}, нужен {MACRO_BASIS}")
        if not gt.get("kcal"):
            errs.append(f"{cid}: у {ref['id']} пустой kcal — сверять нечего")
        if not ref.get("image"):
            errs.append(f"{cid}: у {ref['id']} нет image")
    return errs


@pytest.fixture(scope="module")
def smoke_cases() -> list[dict]:
    return smoke.load_smoke_cases()


@pytest.fixture(scope="module")
def index() -> dict:
    return smoke.a_index()


def test_smoke_cases_are_valid(smoke_cases, index):
    assert check_smoke_cases(smoke_cases, index) == []


@pytest.mark.parametrize(
    "mutate, expect_in",
    [
        (lambda cs: cs[0].update(case="A-999"), "отсутствует в manifest.yaml"),
        (lambda cs: cs[0].update(case="A-07"), "status=unscorable"),
        (lambda cs: cs[0].update(kind="магия"), "kind"),
        (lambda cs: cs[0].update(why=" "), "не записано why"),
    ],
)
def test_smoke_case_validator_catches(smoke_cases, index, mutate, expect_in):
    """Мутанты DoD п.3, включая оба названных в задании: несуществующий id и
    кейс, исключённый из зачёта (`A-07` — `status: unscorable`)."""
    cases = [dict(c) for c in smoke_cases]
    mutate(cases)
    errs = check_smoke_cases(cases, index)
    assert any(expect_in in e for e in errs), errs


def test_cap_case_actually_exceeds_the_cap(smoke_cases):
    """S-05 обязан просить БОЛЬШЕ капа. Иначе негативный контроль проходит
    молча — ровно как знаменатель C6, который стоял ровно на границе и не
    срабатывал (`llm-eval-dataset.md`, разбор яруса C)."""
    caps = [c for c in smoke_cases if c.get("kind") == "cap_reject"]
    assert len(caps) == 1, [c["id"] for c in caps]
    assert int(caps[0]["repeat"]) > settings.api_max_images


def test_photo_cases_cover_kj_first_and_a_clean_control(smoke_cases, index):
    """Состав набора — не случайность (§2 задачи).

    Два `kj-first` нужны, чтобы отличить «модель снова путает кДж с ккал» от
    «одному кейсу не повезло»; `clean` — чтобы отличить «сломался разбор
    этикеток» от «сломался весь путь».
    """
    traps = [index[c["case"]].get("trap")
             for c in smoke_cases if c.get("kind") == "photo"]
    assert traps.count("kj-first") >= 2, traps
    assert "clean" in traps, traps


# ════════════════════ DoD п.6: генератор подписи один ════════════════════════

# Соль протокола Telegram. Собирается из кусков намеренно: иначе ЭТОТ файл сам
# попадает в выдачу сканера и правило пришлось бы ослаблять исключением.
HMAC_MARKER = "Web" + "AppData"


def _py_files() -> list[Path]:
    out: list[Path] = []
    for sub in ("app", "tests", "scripts", "alembic"):
        out += [p for p in (ROOT / sub).rglob("*.py") if "__pycache__" not in p.parts]
    out += list(ROOT.glob("*.py"))
    return out


def _files_containing(marker: str, files: list[Path]) -> set[str]:
    out = set()
    for p in files:
        if marker not in p.read_text(encoding="utf-8", errors="replace"):
            continue
        try:
            out.add(p.relative_to(ROOT).as_posix())
        except ValueError:
            out.add(p.as_posix())   # файл вне репо (tmp_path в контроле сканера)
    return out


def test_initdata_hmac_has_exactly_one_implementation():
    """Где встречается соль протокола, там реализуется HMAC подписи initData.

    Копия алгоритма у смоука была бы вторым экземпляром правды о протоколе — и
    разъехалась бы молча именно у инструмента, чья задача ловить расхождения.
    Ищем голый токен, а не конкретный литерал: копия с одинарными кавычками или
    через `.encode()` — такая же копия.
    """
    owners = _files_containing(HMAC_MARKER, _py_files())
    assert owners == {"app/kernel/auth/initdata.py"}, sorted(owners)


def test_hmac_scanner_can_actually_find_a_copy(tmp_path):
    """Контроль сканера: без него тест выше зелен и на сломанном поиске."""
    copy = tmp_path / "copy.py"
    copy.write_text(f'secret = hmac.new(b"{HMAC_MARKER}", t, sha256)', encoding="utf-8")
    assert _files_containing(HMAC_MARKER, [copy]) == {copy.as_posix()}
    assert _files_containing(HMAC_MARKER, [tmp_path / "copy.py"]) != set()


def test_smoke_and_tests_use_the_shared_generator():
    """Позитивная половина того же правила: потребители зовут общий генератор."""
    for module in ("scripts/smoke.py", "tests/test_auth.py"):
        src = (ROOT / module).read_text(encoding="utf-8")
        assert "make_init_data" in src, module


# ════════════════════ DoD п.5: analyze не трогает БД ═════════════════════════

class _TripwireSession:
    """Сессия, которая взрывается при ЛЮБОМ обращении.

    Свойство «`/analyze` не пишет в дневник» — прямое следствие паттерна
    analyze→confirm→save и единственная причина, по которой смоуку не нужна
    уборка за собой. Свойство неочевидное и может измениться, поэтому оно
    зафиксировано исполнением, а не комментарием в скрипте.
    """

    def __getattr__(self, name):
        raise AssertionError(f"обработчик analyze обратился к сессии: .{name}")


class _FakeRequest(dict):
    def __init__(self, session, body):
        super().__init__()
        self["session"] = session
        self.query = {}
        self.match_info = {}
        self._body = body

    async def json(self):
        return self._body


class _FakeExtractor:
    async def extract(self, **kwargs):
        from app.contexts.nutrition.resolve.ports import ExtractedItem, Macros

        return [ExtractedItem(
            name="гречка", portion_g=200, readout="estimate",
            per100g=Macros(kcal=330, protein=12.6, fat=3.3, carbs=62),
        )]


class _FakeResolver:
    total_budget_s = 10.0

    async def refine(self, ext, allow_network=True):
        from app.contexts.nutrition.resolve.ports import ResolvedNutrition

        return ResolvedNutrition(per100g=ext.per100g, source="estimate")


async def test_analyze_never_touches_the_db_session(monkeypatch):
    monkeypatch.setattr(nutri_routes, "build_extractor", lambda: _FakeExtractor())
    monkeypatch.setattr(nutri_routes, "build_resolver", lambda: _FakeResolver())

    request = _FakeRequest(_TripwireSession(), {"type": "text", "text": "гречка 200 г"})
    response = await nutri_routes.analyze(request)

    assert response.status == 200
    assert json.loads(response.text)["items"]


async def test_tripwire_session_would_catch_a_db_touch():
    """Мутант к тесту выше: сама ловушка обязана срабатывать."""
    with pytest.raises(AssertionError, match="обратился к сессии"):
        await _TripwireSession().execute("select 1")


# ── Под кем ходит смоук: почему реальный id чище технического ────────────────
# Решение С. (2026-08-10): смоук ходит под его собственным telegram id. Ловушка
# выше проверяет ХЕНДЛЕР, но запрос проходит ещё и через auth_middleware, который
# делает `session.commit()` — то есть «в дневник ничего не попадёт» держится не на
# одном свойстве, а на двух. Второе — здесь.

async def _user_count(session) -> int:
    from sqlalchemy import func, select

    from app.kernel.models import User

    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


async def test_existing_user_leaves_no_trace_at_all(session, monkeypatch):
    """Под СУЩЕСТВУЮЩИМ пользователем смоук не пишет ни строки.

    `resolve_viewer` для известного telegram id — чистое чтение: `get_or_create_user`
    возвращает найденного и до `add` не доходит. Вместе с ловушкой выше это и есть
    полная гарантия «смоук не требует уборки за собой».
    """
    from pydantic import SecretStr

    from app.kernel.auth import make_init_data, resolve_viewer
    from app.kernel.models import User

    monkeypatch.setattr(settings, "bot_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "allowed_user_ids_raw", "424242")
    monkeypatch.setattr(settings, "admin_telegram_id", None)
    session.add(User(telegram_user_id=424242, first_name="С."))
    await session.commit()
    before = await _user_count(session)

    await resolve_viewer(session, make_init_data(TOKEN, 424242))

    assert await _user_count(session) == before
    assert not session.new and not session.dirty and not session.deleted


async def test_a_technical_user_would_be_auto_provisioned(session, monkeypatch):
    """Обратная сторона — и причина, по которой «технический пользователь чище»
    оказалось неверно.

    Первый же заход НОВОГО id создаёт строку `User` (auto-provision, ТЗ §8.5) и
    коммитит её middleware. То есть из двух вариантов данные добавляет именно
    технический пользователь, а реальный не добавляет ничего. Если технического
    когда-нибудь заведут — это осознанная строка в БД, а не побочный эффект.
    """
    from pydantic import SecretStr

    from app.kernel.auth import make_init_data, resolve_viewer

    monkeypatch.setattr(settings, "bot_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "allowed_user_ids_raw", "424242,999001")
    monkeypatch.setattr(settings, "admin_telegram_id", None)
    before = await _user_count(session)

    user, _ = await resolve_viewer(session, make_init_data(TOKEN, 999001))

    assert await _user_count(session) == before + 1
    assert user.telegram_user_id == 999001


# ════════════════════ Заглушка прода для мутантов смоука ═════════════════════

def _cap_message(n: int) -> str:
    """Текст отказа берём у ПРОДА, а не переписываем: заглушка обязана говорить
    ровно то, что скажет роут, иначе мутанты проверяют её фантазию."""
    try:
        nutri_routes._check_image_cap([None] * n)
    except InvalidInput as exc:
        return str(exc)
    raise AssertionError(f"{n} снимков не превысили кап — набор мутанта протух")


def _item(kcal: float, *, source: str = "label", source_ref=None, warnings=None) -> dict:
    body = {
        "name": "продукт", "w": None, "kcal": kcal, "p": 1.0, "f": 1.0, "c": 1.0,
        "source": source, "source_ref": source_ref, "matched_name": None,
        "per100g": {"kcal": kcal, "p": 1.0, "f": 1.0, "c": 1.0},
    }
    if warnings:
        body["warnings"] = warnings
    return body


def _ok(kcal: float, **kw) -> tuple[int, dict]:
    return 200, {"items": [_item(kcal, **kw)], "unresolved": []}


def _cap_reject(n: int = 11) -> tuple[int, dict]:
    return 422, {"code": "invalid_input", "message": _cap_message(n)}


@asynccontextmanager
async def stub(responses: list[tuple[int, dict]]):
    """Заглушка прода: health отдаёт настоящий payload, /analyze — ответы по очереди."""
    seen: list[dict] = []
    queue = list(responses)

    async def _health(request):
        return web.json_response(health_payload())

    async def _analyze(request):
        try:
            validate_init_data(request.headers.get("X-Telegram-Init-Data", ""), TOKEN)
        except InitDataError as exc:
            return web.json_response({"code": "auth", "message": str(exc)}, status=401)
        payload = await request.json()
        seen.append(payload)
        if not queue:
            raise AssertionError("заглушке задано меньше ответов, чем сделано вызовов")
        status, body = queue.pop(0)
        return web.json_response(body, status=status)

    app = web.Application(client_max_size=32 * 1024**2)
    app.router.add_get(HEALTH_PATH, _health)
    app.router.add_post(smoke.ANALYZE_PATH, _analyze)
    server = TestServer(app)
    await server.start_server()
    try:
        yield server, seen
    finally:
        await server.close()


# Эталоны яруса A для трёх фото-кейсов набора: заглушка отвечает «как надо».
def _truthful(index: dict, cases: list[dict]) -> list[tuple[int, dict]]:
    out = []
    for case in cases:
        if case["kind"] == "photo":
            gt = index[case["case"]]["ground_truth"]
            out.append(_ok(gt["kcal"], source="label"))
        elif case["kind"] == "text":
            out.append(_ok(330.0, source="estimate"))
        else:
            out.append(_cap_reject(int(case["repeat"])))
    return out


async def _run(server, monkeypatch, tmp_path, extra=()) -> int:
    monkeypatch.setenv("SMOKE_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(smoke, "RUNS_DIR", tmp_path / "runs")
    args = smoke.build_parser().parse_args(
        ["--base-url", str(server.make_url("")), "--user-id", "1",
         "--timeout", "20", *extra]
    )
    return await smoke.run(args)


@pytest.fixture
def cases_and_index(smoke_cases, index):
    return smoke_cases, index


# ── Положительный контроль ────────────────────────────────────────────────────

async def test_smoke_passes_against_a_healthy_stub(
    cases_and_index, monkeypatch, tmp_path, capsys
):
    """Опорная точка. Мутанты ниже ничего не доказывают без неё: смоук, красный
    всегда, так же бесполезен, как смоук, зелёный всегда."""
    cases, index = cases_and_index
    async with stub(_truthful(index, cases)) as (server, seen):
        rc = await _run(server, monkeypatch, tmp_path)

    assert rc == 0, capsys.readouterr().out
    assert len(seen) == len(cases)
    # Подпись смоука приняли НАСТОЯЩИМ валидатором — иначе был бы 401 и rc=1.
    assert all("type" in payload for payload in seen)


async def test_cap_case_really_sends_more_than_the_cap(
    cases_and_index, monkeypatch, tmp_path
):
    """Смоук обязан ДОЙТИ до сервера с 11 кадрами, а не отсеять их у себя."""
    cases, index = cases_and_index
    async with stub(_truthful(index, cases)) as (server, seen):
        await _run(server, monkeypatch, tmp_path)

    cap_case = next(c for c in cases if c["kind"] == "cap_reject")
    assert len(seen[-1]["images"]) == int(cap_case["repeat"])


# ── Мутант 1: мёртвый порт ────────────────────────────────────────────────────

async def test_dead_port_fails_every_case(cases_and_index, monkeypatch, tmp_path, capsys):
    """DoD п.2, мутант 1. Проверяем не только rc=1, но и ЗНАМЕНАТЕЛЬ.

    «0 проверено, всё ок» — это тот же дефект, что дал ложное «0 кДж-утечек у
    всех» при ретропрогоне 02.08: отрицательный результат обязан предъявлять
    размер знаменателя.
    """
    cases, _ = cases_and_index
    monkeypatch.setenv("SMOKE_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(smoke, "RUNS_DIR", tmp_path / "runs")
    args = smoke.build_parser().parse_args(
        ["--base-url", "http://127.0.0.1:1", "--user-id", "1", "--timeout", "2"]
    )
    rc = await smoke.run(args)
    out = capsys.readouterr().out

    assert rc == 1
    assert out.count("[FAIL]") == len(cases) + 1, out
    assert "[ok  ]" not in out, out


# ── Мутант 2: подставной эталон ───────────────────────────────────────────────

async def test_tampered_ground_truth_is_caught(
    cases_and_index, monkeypatch, tmp_path, capsys
):
    """DoD п.2, мутант 2: A-15 с `kcal: 450` — имитация кДж-утечки F-01.

    450 против 110 — это 318%, то есть ровно то, что коридор ±25% обязан ловить
    с запасом. Заглушка при этом отвечает ПРАВИЛЬНЫМИ 110: подменён эталон, а не
    ответ, — иначе тест проверял бы заглушку, а не проверку.
    """
    cases, index = cases_and_index
    tampered = {cid: dict(c) for cid, c in index.items()}
    tampered["A-15"] = {
        **tampered["A-15"],
        "ground_truth": {**tampered["A-15"]["ground_truth"], "kcal": 450},
    }
    monkeypatch.setattr(smoke, "a_index", lambda: tampered)

    async with stub(_truthful(index, cases)) as (server, _):
        rc = await _run(server, monkeypatch, tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "S-01" in out and "отклонение" in out, out


# ── Мутант 3: сервер принял ввод сверх капа ───────────────────────────────────

async def test_cap_reject_fails_when_server_accepts(
    cases_and_index, monkeypatch, tmp_path, capsys
):
    """DoD п.2, мутант 3 — эквивалент `api_max_images=100` на проде.

    Наблюдаемо это одно и то же: сервер отвечает 200 на ввод сверх капа, то есть
    вернулась молчаливая обрезка блока 15.
    """
    cases, index = cases_and_index
    responses = _truthful(index, cases)
    responses[-1] = _ok(56.0)  # вместо отказа — принял и разобрал

    async with stub(responses) as (server, _):
        rc = await _run(server, monkeypatch, tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "S-05" in out and "молчаливая обрезка" in out, out


# ── Мутант 4: поисковая ветка OFF вернулась ───────────────────────────────────

async def test_off_without_barcode_is_caught(
    cases_and_index, monkeypatch, tmp_path, capsys
):
    """Вариант D удалил поиск OFF по названию. `off` законен только как lookup по
    коду, и признак этого — `source_ref` вида `.../product/<цифры>`.

    Проверка не запрещает `off` вовсе: штрихкод в кадре — законный путь, и
    запрет красил бы исправную систему.
    """
    cases, index = cases_and_index
    responses = _truthful(index, cases)
    responses[0] = _ok(110, source="off", source_ref="https://world.openfoodfacts.org/search?q=korejka")

    async with stub(responses) as (server, _):
        rc = await _run(server, monkeypatch, tmp_path)

    assert rc == 1
    assert "поисковая ветка OFF вернулась" in capsys.readouterr().out


async def test_off_by_barcode_stays_green(cases_and_index, monkeypatch, tmp_path):
    """Обратная сторона того же мутанта: законный lookup по коду не краснеет."""
    cases, index = cases_and_index
    responses = _truthful(index, cases)
    gt = index[cases[0]["case"]]["ground_truth"]
    responses[0] = _ok(
        gt["kcal"], source="off",
        source_ref="https://world.openfoodfacts.org/product/4607091387451",
    )

    async with stub(responses) as (server, _):
        assert await _run(server, monkeypatch, tmp_path) == 0


# ── Проверки-одиночки без сети ────────────────────────────────────────────────

def test_health_check_catches_a_stale_commit():
    """Ради этой строки эндпоинт и заводился: «процесс поднялся» ≠ «выкачен код»."""
    payload = {**health_payload(), "commit": "0000000"}
    errs = smoke.check_health(payload, expect_commit="f8eb6e3", expect_model="gpt-5.4-mini")
    assert any("коммит прода" in e for e in errs), errs


def test_health_check_catches_a_stale_model():
    payload = {**health_payload(), "model": "qwen/qwen3.6-27b"}
    errs = smoke.check_health(
        payload, expect_commit=payload["commit"], expect_model="gpt-5.4-mini"
    )
    assert any("модель прода" in e for e in errs), errs


@pytest.mark.parametrize(
    "body, expect_in",
    [
        ({"items": [], "unresolved": []}, "items пуст"),
        ({"items": [_item(100)], "unresolved": [{"name": "x"}]}, "unresolved непуст"),
        ({"items": [{**_item(100), "per100g": None}], "unresolved": []}, "нет per100g"),
        ({"items": [{**_item(100), "source": "web"}], "unresolved": []}, "source="),
        (
            {"items": [_item(100, warnings=["kj_kcal_conflict"])], "unresolved": []},
            "сработали проверки",
        ),
    ],
)
def test_structural_checks_catch_each_failure_mode(body, expect_in):
    """Структурные проверки детерминированы и потому ценнее числовой — но только
    если каждая из них умеет падать."""
    errs = smoke.check_photo_case(body, 100)
    assert any(expect_in in e for e in errs), errs


def test_corridor_is_wide_enough_for_measured_run_to_run_spread():
    """Порог не калиброван под данные — он лежит в РАЗРЫВЕ между двумя измеренными
    величинами, и обе границы стоят здесь числами.

    Снизу: A-13 гулял 8.5% ↔ 14.3% между прогонами ОДНОГО промпта при
    temperature=0.2 — коридор обязан такое пропускать, иначе смоук мигает, а
    мигающий перестают читать. Сверху: F-01 — ошибка в 4.18 раза; её обязан
    ловить любой разумный коридор.
    """
    assert smoke.check_kcal_corridor(110 * 1.143, 110) == []
    assert smoke.check_kcal_corridor(110 * 4.184, 110) != []
    assert 0.15 < smoke.KCAL_CORRIDOR < 0.50


def _results(health_ok: bool, statuses: list[int], case_ok: bool = False) -> list[dict]:
    out = [{"id": "HEALTH", "kind": "health", "status": 200 if health_ok else 404,
            "errors": [] if health_ok else ["нет"]}]
    out += [{"id": f"S-0{i}", "kind": "photo", "status": s,
             "errors": [] if case_ok else ["нет"]}
            for i, s in enumerate(statuses, 1)]
    return out


@pytest.mark.parametrize(
    "results, expect_in",
    [
        (_results(True, [401, 401, 401]), "расходится токен бота, а не выкатка"),
        (_results(True, [403, 403, 403]), "не в whitelist"),
        (_results(False, [0, 0, 0]), "ни один запрос не дошёл"),
        (_results(False, [200, 200, 200], case_ok=True), "выкачен код без эндпоинта версии"),
    ],
)
def test_diagnose_names_the_likely_cause(results, expect_in):
    """Подсказка по КАРТИНЕ отказов, а не по кейсу.

    Заведена по первому же прогону против прода: живой `/health` с верным
    коммитом плюс 401 на всех пяти кейсах — вывод «дело в токене, а не в
    выкатке» приходилось делать вручную, хотя картина однозначная.
    """
    hints = smoke.diagnose(results)
    assert any(expect_in in h for h in hints), hints


def test_diagnose_stays_silent_on_a_green_run():
    """Негативный контроль: подсказка на всё подряд — это шум, а не диагностика.

    Молчание здесь тоже ответ: картина не типовая, думай сам.
    """
    assert smoke.diagnose(_results(True, [200, 200, 200], case_ok=True)) == []
    # Смешанная картина (часть 401, часть 200) — тоже не повод угадывать.
    assert smoke.diagnose(_results(True, [401, 200, 401])) == []


def test_endpoint_whitelist_is_closed():
    """Белый список зашит в коде, а не берётся параметром: параметр однажды
    передадут неверно, и смоук напишет в дневник двух живых людей."""
    assert smoke.ALLOWED_PATHS == frozenset({
        HEALTH_PATH,
        "/api/v1/nutrition/analyze",
        "/api/v1/nutrition/foods/analyze",
    })
    assert not any(
        re.search(r"nutrition/(entries|foods)(?!/analyze)", p)
        for p in smoke.ALLOWED_PATHS
    ), "в белом списке путь, который ПИШЕТ в дневник"


async def test_client_refuses_a_path_outside_the_whitelist():
    """Мутант к белому списку: запрет обязан быть исполняемым, а не декларацией."""
    client = smoke.Client(session=None, base_url="http://x", init_data="")
    with pytest.raises(RuntimeError, match="не в белом списке"):
        await client.post("/api/v1/nutrition/entries", {})


def test_smoke_artifacts_stay_out_of_the_runner_dirs():
    """Хвост №4 блока 11: общий каталог у инструментов с разными контрактами уже
    дважды ронял сводку раннера. У смоука свой."""
    from tests.eval import run_eval

    assert smoke.RUNS_DIR != run_eval.RUNS_DIR
    assert "eval" not in smoke.RUNS_DIR.parts
