"""resolve/cache.py — нормализация ключа дедупа кэша (spec-nutrition-retrieval §11).

Сам upsert/дедуп в `SharedFood` живёт в `FoodService` (нужна сессия и видимость);
здесь — только чистая нормализация ключа, общая для построения и сравнения.

Ключ дедупа: нормализованный `(brand + name + fat_pct)` (lower, trim, ё→е, схлоп
пробелов) ⊕ `barcode` если есть. Без barcode (R1) — это нормализованная строка
вида «бренд имя жирность%».
"""

import re

_WS = re.compile(r"\s+")


def _norm_text(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip().lower().replace("ё", "е")
    return _WS.sub(" ", v)


def normalize_key(
    name: str,
    brand: str | None = None,
    fat_pct: float | None = None,
    barcode: str | None = None,
) -> str:
    """Канонический ключ дедупа кэша. Детерминирован для одной и той же позиции."""
    if barcode:
        return f"ean:{barcode.strip()}"
    parts = [_norm_text(brand), _norm_text(name)]
    if fat_pct is not None:
        # 3.2 → "3.2%", 3.0 → "3%" (срезаем хвостовой ноль для стабильности ключа)
        parts.append(f"{float(fat_pct):g}%")
    return _WS.sub(" ", " ".join(p for p in parts if p)).strip()


def normalize_name(name: str) -> str:
    """Нормализация уже собранного отображаемого имени (для сравнения кандидатов кэша)."""
    return _norm_text(name)
