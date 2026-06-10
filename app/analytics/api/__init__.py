"""REST Mini App Analytics (/api/v1/analytics) — публичная граница (ТЗ §9)."""

from app.analytics.api import routes
from app.analytics.api.routes import register_routes

__all__ = ["register_routes", "routes"]
