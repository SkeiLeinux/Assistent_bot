"""Доменные ошибки kernel — несут HTTP-статус и код для REST (ТЗ §10/§12)."""


class KernelError(Exception):
    status: int = 400
    code: str = "kernel_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class NotWhitelisted(KernelError):
    status = 403
    code = "forbidden"


class AlreadyInFamily(KernelError):
    status = 409
    code = "already_in_family"


class NotInFamily(KernelError):
    status = 404
    code = "not_in_family"


class FamilyFull(KernelError):
    status = 409
    code = "family_full"


class InviteNotFound(KernelError):
    status = 404
    code = "invite_not_found"


class InviteUnusable(KernelError):
    status = 410
    code = "invite_unusable"
