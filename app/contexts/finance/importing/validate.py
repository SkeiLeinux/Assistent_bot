"""Валидация MonthParse перед upsert (ТЗ §7 шаг 4, I3; допуск ≤1 ₽ — Q1-А3).

Структурный инвариант источника. Парковка Q-finance-1: реальный файл (лист Май)
держит ПОМЕСЯЧНЫЙ факт-инвариант, но дневная строка родителя в источнике неполна;
домен хранит факт на листьях (§4.4), родитель — агрегат, поэтому проверяем:
  (a) parent.fact == Σ child.fact         (|Δ| ≤ 1 ₽)
  (b) child.fact  == Σ child.days         (|Δ| ≤ 1 ₽, для каждого ребёнка)
Дневная строка родителя из источника НЕ валидируется (производная, не хранится).
Любое расхождение → fail-loud (ImportStructureError, ничего не пишем).
"""

from __future__ import annotations

from decimal import Decimal

from .errors import ImportIssue, ImportStructureError
from .model import MonthParse, ParsedParent

TOLERANCE = Decimal("1.00")  # допуск инварианта (Q1-А3)


def _check_parent(parent: ParsedParent, sheet: str, issues: list[ImportIssue]) -> None:
    # (a) факт родителя = Σ факт детей.
    sum_children = sum((c.fact for c in parent.children), Decimal("0"))
    if abs(parent.fact - sum_children) > TOLERANCE:
        issues.append(ImportIssue(
            row=0, sheet=sheet,
            reason=(
                f"родитель «{parent.name}»: факт {parent.fact} ≠ Σ детей "
                f"{sum_children} (|Δ|={abs(parent.fact - sum_children)} > {TOLERANCE})"
            ),
        ))
    # (b) факт каждого ребёнка = Σ его дней (дни хранятся на листе).
    for child in parent.children:
        sum_days = sum(child.days.values(), Decimal("0"))
        if abs(child.fact - sum_days) > TOLERANCE:
            issues.append(ImportIssue(
                row=0, sheet=sheet,
                reason=(
                    f"подкатегория «{parent.name}/{child.name}»: факт {child.fact} ≠ "
                    f"Σ дней {sum_days} (|Δ|={abs(child.fact - sum_days)} > {TOLERANCE})"
                ),
            ))


def validate_month(parse: MonthParse) -> None:
    """Проверяет инвариант источника. Расхождение → ImportStructureError (I3)."""
    issues: list[ImportIssue] = []
    for parent in parse.parents:
        _check_parent(parent, parse.sheet_name, issues)
    if issues:
        raise ImportStructureError(
            f"Лист «{parse.sheet_name}»: нарушен инвариант «родитель = Σ детей» "
            f"({len(issues)} расхождений)",
            issues,
        )


def validate_all(parses: list[MonthParse]) -> None:
    """Валидирует все месяцы ДО любой записи (fail-loud без частичного импорта, I3)."""
    for parse in parses:
        validate_month(parse)
