"""Доменные ошибки Finance — несут HTTP-статус/код для REST (зеркалит kernel/lists)."""


class FinanceError(Exception):
    status: int = 400
    code: str = "finance_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class FamilyRequired(FinanceError):
    status = 409
    code = "family_required"


class CategoryNotFound(FinanceError):
    status = 404
    code = "category_not_found"


class InvalidInput(FinanceError):
    status = 422
    code = "invalid_input"


class InvalidResolution(FinanceError):
    status = 422
    code = "invalid_resolution"
