"""Детерминированная нормализация имён таксономии (ТЗ §7 шаг 5, ADR-0002).

Только детерминированно (trim/регистр/пробелы) + таблица алиасов (разрешается в
mapper). НИКАКОГО fuzzy-слияния (I7). `normalize_name` — каноническая форма для
сравнения и поиска алиаса; отображаемое имя сохраняется как пришло (trimmed).
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Канон для сравнения: trim, схлопывание пробелов, нижний регистр."""
    return _WS.sub(" ", name.strip()).lower()


def display_name(name: str) -> str:
    """Отображаемое имя: trim + схлопывание пробелов, регистр как в источнике."""
    return _WS.sub(" ", name.strip())
