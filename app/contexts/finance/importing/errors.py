"""Ошибки Import ACL — fail-loud без частичного импорта (ТЗ §7/§13, I3).

`ImportStructureError` несёт список нераспознанных мест (row/reason) для отчёта
`ImportResultDTO.errors`. Любое структурное несоответствие → 422 на уровне REST.
"""

from dataclasses import dataclass, field


@dataclass
class ImportIssue:
    """Одно нераспознанное место: номер строки (или 0) + причина."""

    row: int
    reason: str
    sheet: str | None = None


class ImportError_(Exception):
    """База ошибок импорта (имя с подчёркиванием — не затенять builtin ImportError)."""

    status: int = 422
    code: str = "finance_import_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass
class _IssueCarrier:
    issues: list[ImportIssue] = field(default_factory=list)


class ImportStructureError(ImportError_):
    """Структура листа не распознана / нарушен инвариант. Ничего не пишем (I3)."""

    code = "finance_import_structure"

    def __init__(self, message: str, issues: list[ImportIssue] | None = None) -> None:
        super().__init__(message)
        self.issues: list[ImportIssue] = issues or []
