"""kernel-модели идентичности и контракта видимости.

Импорт регистрирует таблицы в общем Base.metadata (для alembic-autogenerate).
"""

from app.kernel.models.family import Family, Membership
from app.kernel.models.invite import Invite, InviteStatus
from app.kernel.models.shareable import Hideable, Scope, Scoped, Shareable
from app.kernel.models.user import User

__all__ = [
    "User",
    "Family",
    "Membership",
    "Invite",
    "InviteStatus",
    "Shareable",
    "Scoped",
    "Hideable",
    "Scope",
]
