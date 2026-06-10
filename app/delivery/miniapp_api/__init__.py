"""REST мини-апы (монтаж aiohttp).

Волна A: kernel REST (/api/v1/*) + Lists (/api/v1/lists,/items) + Finance
(/api/v1/finance/*) + Nutrition (/api/v1/nutrition/*).
Волна B: + Reminders (/api/v1/reminders/*) + Analytics (/api/v1/analytics/*).
Старый food api.py удалён — функциональность переехала в contexts.nutrition.api.
"""

from .web import create_app, start_web_server

__all__ = ["create_app", "start_web_server"]
