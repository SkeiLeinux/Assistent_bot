"""Import ACL — заменяемый адаптер xlsx → домен (ТЗ §7, ADR-0002, ADR-0004).

ЕДИНСТВЕННАЯ внешняя граница Finance. openpyxl и знание формы таблицы заперты
здесь (A6). Публичная функция `import_xlsx(session, family_id, data, year)`:
  parse → validate(все месяцы) → map(upsert по месяцу).
Fail-loud: структурное несоответствие → ImportStructureError ДО любой записи (I3);
вызывающий (сервис/REST) делает commit при успехе, rollback при ошибке.

Этот пакет импортируется ТОЛЬКО изнутри `finance` (import-linter).
"""

from __future__ import annotations

from .errors import ImportIssue, ImportStructureError
from .mapper import DomainMapper, ImportOutcome, MonthResult
from .model import MonthParse
from .parser import parse_workbook
from .validate import validate_all

__all__ = [
    "import_xlsx",
    "ImportOutcome",
    "MonthResult",
    "ImportStructureError",
    "ImportIssue",
    "MonthParse",
]


async def import_xlsx(
    session, family_id: int, data: bytes, year: int
) -> tuple[ImportOutcome, list[ImportIssue]]:
    """Полный поток импорта. Возвращает (результат, пропущенные листы).

    Бросает ImportStructureError при структурном несоответствии (fail-loud, I3) —
    к этому моменту ничего не записано (валидация всех месяцев — до маппера).
    """
    months, skipped = parse_workbook(data, year)
    validate_all(months)  # fail-loud ДО любой мутации
    mapper = DomainMapper(session, family_id)
    outcome = await mapper.apply(months)
    return outcome, skipped
