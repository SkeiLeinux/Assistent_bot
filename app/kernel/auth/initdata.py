"""kernel.auth.initdata — валидация Telegram WebApp initData (ТЗ §8, current-state).

Когда пользователь открывает мини-апу, Telegram передаёт строку `initData`
(query-string с данными пользователя и подписью `hash`). Подпись считается на
стороне Telegram по секрету из токена бота — подделать, не зная токен, нельзя.

Алгоритм проверки (документация Telegram):
  1. Разобрать initData в пары ключ=значение (значения URL-декодированы).
  2. Вынуть `hash`.
  3. data_check_string = оставшиеся пары, отсортированные по ключу, "key=value"\n.
  4. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token).
  5. computed = HMAC_SHA256(key=secret_key, msg=data_check_string) в hex.
  6. const-time сверка computed с присланным hash.

max-age `auth_date` = 1 месяц (ТЗ §8 OQ-3): редкая ре-авторизация в пользу UX
при модели угроз из двух whitelisted-пользователей.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode

# 1 месяц (ТЗ §8, OQ-3) — поднято с прежних 24ч.
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class InitDataError(Exception):
    """initData отсутствует, просрочен или подпись не сошлась."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, str]:
    """Проверить подпись initData и вернуть разобранные поля.

    Бросает InitDataError, если строка пустая, подпись не сошлась или auth_date
    слишком старый. max_age_seconds=0 отключает проверку возраста.
    """
    if not init_data:
        raise InitDataError("initData пустой")

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


def sign_init_data(fields: Mapping[str, str], bot_token: str) -> str:
    """Собрать подписанный initData из полей — обратная половина `validate_init_data`.

    **Почему подписывающая половина живёт в проде, а не в тестах.** Подписи
    initData нужны двум потребителям: тестам auth и смоуку выкатки
    (`scripts/smoke.py`), который ходит по REST задеплоенного инстанса. Копия
    алгоритма у каждого — это второй экземпляр правды о протоколе: поменяется
    порядок полей или состав `data_check_string`, и копия разъедется молча,
    причём разъедется именно у инструмента, чья задача — ловить расхождение
    прода с кодом. Поэтому обе половины протокола стоят в одном файле и меняются
    одной правкой; шаги 3–5 ниже — построчное зеркало шагов 3–5 валидатора.

    Новой возможности это не даёт: подпись требует токен бота, а с токеном и так
    можно всё.
    """
    data = {k: str(v) for k, v in fields.items() if k != "hash"}
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(data)


def make_init_data(
    bot_token: str,
    user_id: int,
    *,
    first_name: str = "T",
    username: str = "t",
    auth_date: int | None = None,
    query_id: str = "AAH-test",
) -> str:
    """Готовый подписанный initData для пользователя — то, что шлёт Telegram.

    Форма объекта `user` — тоже часть протокола (её читает `parse_user`), поэтому
    собирается здесь, а не у каждого вызывающего. `separators` без пробелов: так
    сериализует Telegram, и так же обязан сериализовать тот, кто подделывает
    строку под него.
    """
    user = json.dumps(
        {"id": user_id, "first_name": first_name, "username": username},
        separators=(",", ":"),
    )
    return sign_init_data(
        {
            "user": user,
            "auth_date": str(int(time.time()) if auth_date is None else auth_date),
            "query_id": query_id,
        },
        bot_token,
    )


def parse_user(data: dict[str, str]) -> dict:
    """Достать объект пользователя (поле `user` — JSON-строка)."""
    raw = data.get("user")
    if not raw:
        raise InitDataError("в initData нет user")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InitDataError("user в initData не разобрался как JSON") from exc
