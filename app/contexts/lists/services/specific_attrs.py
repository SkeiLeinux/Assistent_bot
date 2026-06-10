"""Валидация type-специфичных полей элемента (specific_attrs) против схемы на тип (I5).

Схема на тип = реестр допустимых ключей и их типов значений. Неизвестный ключ или
неверный тип → 422. Жёсткой БД-схемы нет: добавление типа/ключа — правка реестра без
миграций (L5). Стартовый набор минимален и расширяем по продуктовым требованиям.
"""

from app.contexts.lists.models import ListType
from app.contexts.lists.services.errors import InvalidSpecificAttrs

# Тип → {ключ: допустимый python-тип значения (после json-парсинга)}.
_NUMERIC = (int, float)
_SCHEMAS: dict[ListType, dict[str, type | tuple[type, ...]]] = {
    ListType.TASK: {"due_date": str, "priority": str},
    ListType.SHOPPING: {"quantity": (int, float, str), "unit": str, "price": _NUMERIC},
    ListType.IDEA: {"url": str, "note": str},
    ListType.MOVIE: {"year": int, "rating": _NUMERIC, "genre": str, "watched_on": str},
    ListType.GIFT: {"price": _NUMERIC, "url": str, "for_whom": str},
    ListType.LINK: {"url": str, "title": str},
}


def validate_specific_attrs(list_type: ListType, attrs: dict | None) -> dict | None:
    """Проверяет attrs против схемы list_type. Возвращает attrs (или None). Иначе 422."""
    if attrs is None:
        return None
    if not isinstance(attrs, dict):
        raise InvalidSpecificAttrs("specific_attrs должен быть объектом")

    schema = _SCHEMAS.get(list_type, {})
    unknown = set(attrs) - set(schema)
    if unknown:
        allowed = ", ".join(sorted(schema)) or "—"
        raise InvalidSpecificAttrs(
            f"Недопустимые поля для типа {list_type.value}: "
            f"{', '.join(sorted(unknown))}. Допустимо: {allowed}"
        )

    for key, value in attrs.items():
        if value is None:
            continue
        expected = schema[key]
        # bool — подкласс int; не считаем его числом/строкой по ошибке.
        if isinstance(value, bool) or not isinstance(value, expected):
            raise InvalidSpecificAttrs(f"Поле «{key}» имеет недопустимый тип значения")
    return attrs
