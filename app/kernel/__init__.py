"""Identity & Sharing (shared kernel) — публичная поверхность (ТЗ §11).

Разрешено импортировать контекстам: User/Family/Membership/Invite, Shareable,
visible_to, current_viewer, VisibilityDTO. Внутренние repositories/auth-детали —
не часть поверхности (но доступны delivery как транспортному слою).
"""

from app.kernel.auth import (
    InitDataError,
    parse_user,
    resolve_viewer,
    validate_init_data,
)
from app.kernel.models import (
    Family,
    Invite,
    InviteStatus,
    Membership,
    Scope,
    Shareable,
    User,
)
from app.kernel.services import IdentityService, PairingService
from app.kernel.sharing import (
    ShareableRepository,
    Viewer,
    VisibilityDTO,
    VisibilityError,
    visible_to,
)

# Имя из ТЗ §11 для резолвера текущего зрителя.
current_viewer = resolve_viewer

__all__ = [
    # идентичность и видимость
    "User",
    "Family",
    "Membership",
    "Invite",
    "InviteStatus",
    "Shareable",
    "Scope",
    "visible_to",
    "Viewer",
    "VisibilityDTO",
    "VisibilityError",
    "ShareableRepository",
    # auth
    "current_viewer",
    "resolve_viewer",
    "validate_init_data",
    "parse_user",
    "InitDataError",
    # сервисы (для delivery)
    "IdentityService",
    "PairingService",
]
