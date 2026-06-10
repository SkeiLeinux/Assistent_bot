"""Перечисления контекста Finance (ТЗ §4)."""

import enum


class CategoryStatus(str, enum.Enum):
    """Статус статьи расходов (§4.1, §8).

    NEEDS_REVIEW — невиданная при импорте категория (I7): создаётся как есть,
    ждёт подтверждения/алиаса пользователем. ACTIVE — подтверждённая.
    """

    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"


class IncomeKind(str, enum.Enum):
    """Род источника дохода (§4.5)."""

    CARRYOVER = "carryover"  # «Переход с предыдущего месяца»
    PARTNER = "partner"  # участник семьи (Катя/Сережа), опц. линк на User
    CASHBACK = "cashback"  # «Кэшбек»
    OTHER = "other"  # «Прочее» и пр.
