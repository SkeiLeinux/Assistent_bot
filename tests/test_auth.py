"""A4 — auth: битый/просроченный initData → InitDataError (401); не в whitelist → 403.

Резолв зрителя проверяется напрямую (standalone-шов: домен получает уже
резолвнутого viewer, не зная о Telegram).
"""

import time

import pytest
from pydantic import SecretStr

from app.config import settings
from app.kernel.auth import InitDataError, make_init_data, resolve_viewer
from app.kernel.services.errors import NotWhitelisted

_TOKEN = "TESTTOKEN"


def _init_data(user_id: int, auth_date: int | None = None) -> str:
    """Тонкая обёртка над ЕДИНСТВЕННЫМ генератором подписи (`kernel.auth.initdata`).

    Здесь стояла своя реализация HMAC — она же понадобилась смоуку выкатки, и две
    копии протокола разъехались бы молча. Обёртка оставлена только затем, чтобы не
    переписывать вызовы ниже: токен у всех тестов файла один.
    """
    return make_init_data(_TOKEN, user_id, auth_date=auth_date)


@pytest.fixture
def fake_auth(monkeypatch):
    # SecretStr, как в конфиге: resolve_viewer снимает его .get_secret_value().
    monkeypatch.setattr(settings, "bot_token", SecretStr(_TOKEN))
    monkeypatch.setattr(settings, "allowed_user_ids_raw", "1,2")
    monkeypatch.setattr(settings, "admin_telegram_id", None)


async def test_valid_initdata_resolves_viewer(session, fake_auth):
    user, viewer = await resolve_viewer(session, _init_data(1))
    assert user.telegram_user_id == 1
    assert viewer.id == user.id
    assert viewer.family_id is None


async def test_bad_hash_rejected(session, fake_auth):
    tampered = _init_data(1).rsplit("hash=", 1)[0] + "hash=deadbeef"
    with pytest.raises(InitDataError):
        await resolve_viewer(session, tampered)


async def test_expired_auth_date_rejected(session, fake_auth):
    old = int(time.time()) - 40 * 24 * 3600  # 40 дней > 30
    with pytest.raises(InitDataError):
        await resolve_viewer(session, _init_data(1, auth_date=old))


async def test_empty_initdata_rejected(session, fake_auth):
    with pytest.raises(InitDataError):
        await resolve_viewer(session, "")


async def test_non_whitelisted_rejected(session, fake_auth):
    with pytest.raises(NotWhitelisted):
        await resolve_viewer(session, _init_data(999))
