"""`GET /api/v1/health` — эндпоинт версии (задача смоука §1).

Предмет три штуки, и все три — про то, чем health отличается от «сервис активен»:

  - **состав ответа зафиксирован списком.** Публичный эндпоинт растёт по одному
    полю за раз; без пина в него однажды попадёт путь, версия библиотеки или
    содержимое `.env`. Пин двусторонний: и лишнее, и недостающее поле красят;
  - **секретов нет — и детектор этого умеет падать.** Правило «ключ не в ответе»
    без негативного контроля остаётся комментарием (`llm-eval-dataset.md` §3D.1).
    Тот же класс, что утечка `GROQ_API_KEY` в `journalctl` 02.08.26;
  - **health не роняет процесс.** Битый или отсутствующий `.git` даёт
    `"unknown"`, а не исключение: эндпоинт, из-за которого сервис не поднялся,
    хуже отсутствующего.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from app.config import settings
from app.delivery.miniapp_api import health as health_mod
from app.delivery.miniapp_api.health import (
    HEALTH_FIELDS,
    HEALTH_PATH,
    UNKNOWN_COMMIT,
    health_payload,
    read_commit,
    register_health_routes,
)
from app.delivery.miniapp_api.web import PUBLIC_API_PATHS, auth_middleware

_SHA = "f8eb6e3ab1c2d3e4f5061728394a5b6c7d8e9f01"


# ── Состав ответа ────────────────────────────────────────────────────────────

def test_payload_keys_are_exactly_the_declared_set():
    """В обе стороны: ни лишнего поля, ни потерянного."""
    assert set(health_payload()) == set(HEALTH_FIELDS)


def test_payload_reads_config_and_does_not_hardcode(monkeypatch):
    """Смысл эндпоинта — показать, что РЕАЛЬНО загрузилось.

    Константа в теле показывала бы то, что написано в коде, то есть отвечала бы
    на другой вопрос — и отвечала бы «да» даже на процессе со старым `.env`.
    """
    monkeypatch.setattr(settings, "llm_vision_model", "model-from-env")
    monkeypatch.setattr(settings, "llm_fallback_vision_model", "fallback-from-env")
    monkeypatch.setattr(settings, "api_max_images", 42)
    monkeypatch.setattr(settings, "llm_max_images", 7)

    body = health_payload()
    assert body["model"] == "model-from-env"
    assert body["fallback_model"] == "fallback-from-env"
    assert body["api_max_images"] == 42
    assert body["llm_max_images"] == 7


def test_commit_is_frozen_at_import_not_read_per_request(monkeypatch):
    """Коммит читается ОДИН раз, при импорте, и это не оптимизация.

    Питон не перечитывает исходники после `git pull`, поэтому работающий процесс
    несёт код, с которым стартовал. Читай мы `.git` на каждый запрос — health
    после `git pull` без рестарта отрапортовал бы НОВЫЙ коммит, работая на
    старом коде, то есть соврал бы ровно в том сценарии, ради которого заведён.
    """
    monkeypatch.setattr(health_mod, "read_commit", lambda *a, **k: "0000000")
    assert health_payload()["commit"] == health_mod.COMMIT != "0000000"


# ── Негативный контроль: секреты ─────────────────────────────────────────────

_SECRET_KEY = "sk-proj-DO-NOT-LEAK-0123456789"
_SECRET_TOKEN = "7777777:AAH-DO-NOT-LEAK-abcdefgh"


def _leaks(payload: dict, secrets: list[str]) -> list[str]:
    """Что из секретов просочилось в тело — целиком или хвостом.

    Хвост проверяется отдельно: «последние 8 символов ключа для диагностики» —
    самый частый способ вернуть утечку, считая, что её нет.
    """
    text = json.dumps(payload, ensure_ascii=False)
    found = []
    for secret in secrets:
        if not secret:
            continue
        if secret in text or (len(secret) >= 8 and secret[-8:] in text):
            found.append(secret)
    return found


@pytest.fixture
def secret_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(_SECRET_KEY))
    monkeypatch.setattr(settings, "bot_token", SecretStr(_SECRET_TOKEN))
    return [_SECRET_KEY, _SECRET_TOKEN, settings.database_url]


def test_no_secret_reaches_the_response(secret_settings):
    """DoD п.4: ни ключа, ни токена — ни целиком, ни хвостом. Плюс `database_url`:
    путь к БД тоже не то, что раздают публично."""
    assert _leaks(health_payload(), secret_settings) == []


def test_leak_detector_can_actually_fail(secret_settings):
    """Контроль самого контроля.

    Без него тест выше зелен ни о чём: он проверяет функцию, которая, может
    статься, ничего не ищет. Оба мутанта — реальные способы утечь: поле «на
    всякий случай» и «безопасный» хвост ключа.
    """
    whole = {**health_payload(), "key": _SECRET_KEY}
    assert _leaks(whole, secret_settings) == [_SECRET_KEY]

    tail = {**health_payload(), "key_hint": "…" + _SECRET_KEY[-8:]}
    assert _leaks(tail, secret_settings) == [_SECRET_KEY]


# ── Чтение `.git` без subprocess ─────────────────────────────────────────────

def _git(root, head: str, refs: dict[str, str] | None = None, packed: str = ""):
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(head, encoding="utf-8")
    for ref, sha in (refs or {}).items():
        path = git / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return root


def test_reads_loose_ref(tmp_path):
    _git(tmp_path, "ref: refs/heads/master\n", {"refs/heads/master": _SHA})
    assert read_commit(tmp_path) == _SHA[:7]


def test_reads_detached_head(tmp_path):
    _git(tmp_path, _SHA + "\n")
    assert read_commit(tmp_path) == _SHA[:7]


def test_reads_packed_refs_on_a_fresh_clone(tmp_path):
    """Свежий клон держит ветки в `packed-refs`, отдельного файла ref нет.

    Деплой сейчас — `git pull` в существующий каталог, но переезд на новую
    машину даёт именно эту раскладку, и `"unknown"` там означал бы «смоук не
    может проверить версию» ровно в момент, когда это нужнее всего.
    """
    _git(
        tmp_path,
        "ref: refs/heads/master\n",
        packed=f"# pack-refs with: peeled\n{_SHA} refs/heads/master\n",
    )
    assert read_commit(tmp_path) == _SHA[:7]


def test_reads_gitdir_indirection(tmp_path):
    """`.git` как файл (worktree/submodule) — `gitdir: <путь>`."""
    real = tmp_path / "real-git"
    real.mkdir()
    (real / "HEAD").write_text(_SHA + "\n", encoding="utf-8")
    (tmp_path / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    assert read_commit(tmp_path) == _SHA[:7]


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda root: root, id="нет .git вовсе"),
        pytest.param(
            lambda root: _git(root, "ref: refs/heads/gone\n"), id="ref потерялся"
        ),
        pytest.param(lambda root: _git(root, ""), id="пустой HEAD"),
        pytest.param(lambda root: _git(root, "не-sha\n"), id="мусор вместо sha"),
        pytest.param(
            lambda root: _git(root, "ref: refs/heads/m\n", {"refs/heads/m": "zzz"}),
            id="не-hex в ref",
        ),
    ],
)
def test_broken_git_gives_unknown_and_never_raises(tmp_path, make):
    """Health, роняющий процесс, хуже отсутствующего health."""
    assert read_commit(make(tmp_path)) == UNKNOWN_COMMIT


# ── Маршрут: 200 без initData ────────────────────────────────────────────────

async def test_health_is_served_without_initdata():
    """Без auth — осознанно: иначе смоук не сможет проверить версию до того, как
    убедится, что auth работает, и перепутает два разных отказа (§1 задачи)."""
    app = web.Application(middlewares=[auth_middleware])
    register_health_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(HEALTH_PATH)
        assert resp.status == 200
        assert set(await resp.json()) == set(HEALTH_FIELDS)


async def test_other_api_paths_still_require_initdata(monkeypatch):
    """Мутант к исключению: список публичных путей не должен «протечь» на /api/.

    Проверяется тем же middleware и тем же запросом без заголовка — разница
    только в пути.
    """
    app = web.Application(middlewares=[auth_middleware])
    register_health_routes(app)

    async def _me(request):
        raise AssertionError("хендлер не должен быть вызван без initData")

    app.router.add_get("/api/v1/me", _me)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/me")
        assert resp.status == 401


def test_public_paths_list_is_exactly_health():
    """Каждая запись здесь — публичная поверхность, поэтому список пинуется целиком."""
    assert PUBLIC_API_PATHS == frozenset({HEALTH_PATH})
