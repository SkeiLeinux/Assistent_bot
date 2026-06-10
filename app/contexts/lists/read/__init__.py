"""Read-сторона Lists — единственная граница для Analytics (ADR-0004, A6)."""

from .queries import analytics_by_type, recent_changes

__all__ = ["analytics_by_type", "recent_changes"]
