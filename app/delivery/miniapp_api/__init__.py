"""REST мини-апы (монтаж aiohttp).

Срез 2: kernel REST (/api/v1/*) + Lists REST (/api/v1/lists,/items). Эндпоинты еды
(api.py) сняты с проводки до миграции Nutrition (S2c) — файл остаётся, не импортируется.
"""

from .web import create_app, start_web_server

__all__ = ["create_app", "start_web_server"]
