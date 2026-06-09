"""kernel.sharing — механизм видимости (миксин Shareable живёт в kernel.models)."""

from app.kernel.sharing.repository import ShareableRepository
from app.kernel.sharing.visibility import (
    Viewer,
    VisibilityDTO,
    VisibilityError,
    visible_to,
)

__all__ = [
    "Viewer",
    "VisibilityDTO",
    "VisibilityError",
    "visible_to",
    "ShareableRepository",
]
