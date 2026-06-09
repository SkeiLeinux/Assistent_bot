"""REST мини-апы (монтаж aiohttp).

Срез 1: отдаём только kernel REST (/api/v1/*). Эндпоинты еды (api.py) сняты с
проводки до миграции Nutrition в Срезе 2 — файл остаётся, не импортируется.
"""

from .kernel_api import create_app, start_web_server

__all__ = ["create_app", "start_web_server"]
