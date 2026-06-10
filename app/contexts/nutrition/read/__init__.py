"""Read-сторона Nutrition — единственная граница для Analytics (ADR-0004, A6)."""

from .queries import MacroTotals, macros_by_day, macros_total

__all__ = ["macros_by_day", "macros_total", "MacroTotals"]
