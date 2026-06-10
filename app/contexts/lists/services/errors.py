"""Доменные ошибки Lists — несут HTTP-статус/код для REST (зеркалит kernel.KernelError).

Видимостные нарушения (I7–I9) поднимаются kernel-`VisibilityError` (→ 422) — не дублируем.
"""


class ListError(Exception):
    status: int = 400
    code: str = "list_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ListNotFound(ListError):
    status = 404
    code = "list_not_found"


class ItemNotFound(ListError):
    status = 404
    code = "item_not_found"


class FamilyRequired(ListError):
    status = 409
    code = "family_required"


class CrossListNesting(ListError):
    status = 422
    code = "cross_list_nesting"


class MaxDepthExceeded(ListError):
    status = 422
    code = "max_depth_exceeded"


class InvalidParent(ListError):
    status = 422
    code = "invalid_parent"


class InvalidSpecificAttrs(ListError):
    status = 422
    code = "invalid_specific_attrs"


class InvalidInput(ListError):
    status = 422
    code = "invalid_input"
