"""A7 — VisibilityDTO отклоняет нарушения I7–I9 (REST мапит в 422)."""

import pytest
from pydantic import ValidationError

from app.kernel.models.shareable import Scope
from app.kernel.sharing import VisibilityDTO, VisibilityError


def test_personal_with_hidden_rejected():
    # I7 — проверка в самом DTO; pydantic оборачивает в ValidationError.
    with pytest.raises(ValidationError):
        VisibilityDTO(scope=Scope.PERSONAL, hidden_from=[5])


def test_owner_in_hidden_rejected():
    dto = VisibilityDTO(scope=Scope.FAMILY, hidden_from=[1])
    with pytest.raises(VisibilityError):  # I8
        dto.validate_against(owner_id=1, family_member_ids=[1, 2])


def test_hidden_non_member_rejected():
    dto = VisibilityDTO(scope=Scope.FAMILY, hidden_from=[3])
    with pytest.raises(VisibilityError):  # I9
        dto.validate_against(owner_id=1, family_member_ids=[1, 2])


def test_valid_family_gift_ok():
    dto = VisibilityDTO(scope=Scope.FAMILY, hidden_from=[2])
    dto.validate_against(owner_id=1, family_member_ids=[1, 2])  # без исключения
