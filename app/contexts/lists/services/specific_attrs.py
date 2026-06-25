"""Валидация type-специфичных полей элемента (specific_attrs) против схемы на тип (I5).

Схема на тип = реестр допустимых ключей и их типов значений. Неизвестный ключ или
неверный тип → 422. Жёсткой БД-схемы нет: добавление типа/ключа — правка реестра без
миграций (L5). Стартовый набор минимален и расширяем по продуктовым требованиям.

v0.4 (§4.5): TASK.priority — член enum TaskPriority (не свободный текст); LINK.comment
вместо прежнего link-title; ключ tags (список строк) для TASK/IDEA/GIFT/LINK (Q1-А9).
"""

from app.contexts.lists.models import ListType, TaskPriority
from app.contexts.lists.services.errors import InvalidSpecificAttrs

_NUMERIC = (int, float)

# Спец-маркеры значений (валидируются отдельно от простого isinstance):
_PRIORITY = object()  # значение — член TaskPriority (по .value)
_TAGS = object()      # значение — список строк

_PRIORITY_VALUES = frozenset(p.value for p in TaskPriority)

# Тип → {ключ: допустимый python-тип значения (после json-парсинга) | спец-маркер}.
_SCHEMAS: dict[ListType, dict[str, object]] = {
    ListType.TASK: {"due_date": str, "priority": _PRIORITY, "tags": _TAGS},
    ListType.SHOPPING: {"quantity": (int, float, str), "unit": str, "price": _NUMERIC},
    ListType.IDEA: {"url": str, "note": str, "tags": _TAGS},
    ListType.MOVIE: {"year": int, "rating": _NUMERIC, "genre": str, "watched_on": str},
    ListType.GIFT: {"price": _NUMERIC, "url": str, "for_whom": str, "tags": _TAGS},
    ListType.LINK: {"url": str, "comment": str, "tags": _TAGS},
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
        if expected is _PRIORITY:
            if not isinstance(value, str) or value not in _PRIORITY_VALUES:
                raise InvalidSpecificAttrs(
                    "Поле «priority» должно быть одним из: "
                    + ", ".join(sorted(_PRIORITY_VALUES))
                )
            continue
        if expected is _TAGS:
            if not isinstance(value, list) or any(
                isinstance(t, bool) or not isinstance(t, str) for t in value
            ):
                raise InvalidSpecificAttrs("Поле «tags» должно быть списком строк")
            continue
        # bool — подкласс int; не считаем его числом/строкой по ошибке.
        if isinstance(value, bool) or not isinstance(value, expected):
            raise InvalidSpecificAttrs(f"Поле «{key}» имеет недопустимый тип значения")
    return attrs
