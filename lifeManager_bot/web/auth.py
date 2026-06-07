"""
web/auth.py — валидация Telegram WebApp initData.

Когда пользователь открывает мини-апу, Telegram передаёт во фронт строку
`initData` (window.Telegram.WebApp.initData). Это query-string с данными
пользователя и подписью `hash`. Подпись считается на стороне Telegram по
секрету, выведенному из токена бота, поэтому подделать её, не зная токен,
нельзя — а значит initData можно доверять как аутентификации.

Алгоритм проверки (из документации Telegram):
  1. Разобрать initData в пары ключ=значение (значения уже URL-декодированы).
  2. Вынуть из них `hash`.
  3. Собрать data_check_string: оставшиеся пары, отсортированные по ключу,
     в формате "key=value", склеенные через '\n'.
  4. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token).
  5. computed = HMAC_SHA256(key=secret_key, msg=data_check_string) в hex.
  6. computed должен совпасть с присланным hash (сравнение const-time).

Дополнительно проверяем свежесть auth_date — чтобы перехваченный старый
initData нельзя было переиспользовать бесконечно.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    """initData отсутствует, просрочен или подпись не сошлась."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 24 * 60 * 60,
) -> dict[str, str]:
    """Проверить подпись initData и вернуть разобранные поля.

    Бросает InitDataError, если строка пустая, подпись не сошлась или
    auth_date слишком старый. max_age_seconds=0 отключает проверку возраста.
    """
    if not init_data:
        raise InitDataError("initData пустой")

    # parse_qsl сохраняет порядок и URL-декодирует значения — именно
    # декодированные значения должны попасть в data_check_string.
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InitDataError("в initData нет hash")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataError("подпись initData не совпала")

    if max_age_seconds:
        auth_date = data.get("auth_date", "")
        if auth_date.isdigit():
            age = time.time() - int(auth_date)
            if age > max_age_seconds:
                raise InitDataError("initData просрочен")

    return data


def parse_user(data: dict[str, str]) -> dict:
    """Достать объект пользователя (поле `user` — это JSON-строка)."""
    raw = data.get("user")
    if not raw:
        raise InitDataError("в initData нет user")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InitDataError("user в initData не разобрался как JSON") from exc
