"""kernel.sharing — механизм видимости (миксин Shareable живёт в kernel.models)."""

from app.kernel.sharing.repository import ShareableRepository
from app.kernel.sharing.visibility import (
    Viewer,
    VisibilityDTO,
    VisibilityError,
    not_hidden,
    scope_visible,
    visible_to,
)

__all__ = [
    "Viewer",
    "VisibilityDTO",
    "VisibilityError",
    "visible_to",
    "scope_visible",
    "not_hidden",
    "ShareableRepository",
]
