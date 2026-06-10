"""Read-проекции Finance для Analytics (ТЗ §12). Импорт: только kernel/db."""

from .queries import (
    CategoryAggregate,
    FinanceReadResult,
    IncomeAggregate,
    daily_rows,
    finance_aggregate,
)

__all__ = [
    "finance_aggregate",
    "daily_rows",
    "FinanceReadResult",
    "CategoryAggregate",
    "IncomeAggregate",
]
