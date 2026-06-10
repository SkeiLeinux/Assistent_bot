"""Время для моделей (leaf-утилита db).

`utcnow_naive` — наивный UTC под naive DateTime-колонки. Используется как Python-side
`onupdate`: значение известно сразу после flush и НЕ экспайрится (в отличие от
server-side `func.now()` onupdate, который в async ловит MissingGreenlet при доступе
к атрибуту после UPDATE). server_default остаётся `func.now()` для значения при INSERT.
"""

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
