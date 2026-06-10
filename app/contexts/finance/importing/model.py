"""Промежуточное представление импорта (pre-domain, ТЗ §7 шаг 3).

`MonthParse` — структурированный, но ещё не доменный результат парсинга одного
листа: месяц, источники дохода, дерево категорий с планом/фактом/днями. openpyxl
здесь НЕ участвует — это чистые dataclass'ы (заменяемость ACL: домен/валидатор не
знают про xlsx). Числа — Decimal (точность денег, без float-дрейфа).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class ParsedIncome:
    """Строка-источник дохода из блока A–D."""

    name: str
    kind: str  # значение IncomeKind (carryover/partner/cashback/other)
    plan: Decimal | None
    fact: Decimal | None


@dataclass
class ParsedCategory:
    """Подкатегория (лист дерева) с планом, фактом и дневной строкой."""

    name: str
    plan: Decimal | None
    fact: Decimal
    # Разрежённая дневная строка: {день_месяца(1..31): сумма}. Только ненулевые.
    days: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class ParsedParent:
    """Категория-родитель (заливка E=FFF9CB9C) + её подкатегории."""

    name: str
    plan: Decimal | None
    fact: Decimal
    days: dict[int, Decimal] = field(default_factory=dict)
    children: list[ParsedCategory] = field(default_factory=list)


@dataclass
class MonthParse:
    """Разобранный лист = месяц."""

    month: date  # первый день месяца (год — параметр импорта)
    sheet_name: str
    income: list[ParsedIncome] = field(default_factory=list)
    parents: list[ParsedParent] = field(default_factory=list)
