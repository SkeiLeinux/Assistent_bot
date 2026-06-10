"""S2b Finance — A6: изоляция/заменяемость ACL.

openpyxl и xlsx-специфика разрешены ТОЛЬКО в `finance/importing` и `finance/exporting`;
домен/аналитика/read/api/repositories их не импортируют (ADR-0002/0004, заменяемость).
Проверяем статически по исходникам (контракт import-linter добавляет интегратор —
build-contract §6.2; этот тест ловит нарушение независимо от состояния pyproject).
"""

from __future__ import annotations

import ast
from pathlib import Path

FINANCE_ROOT = Path("app/contexts/finance")
ALLOWED_XLSX_DIRS = {"importing", "exporting"}


def _imports_openpyxl(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "openpyxl" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "openpyxl":
                return True
    return False


def test_openpyxl_confined_to_importing_and_exporting():
    """A6: ни один модуль Finance вне importing/exporting не импортирует openpyxl."""
    offenders = []
    for py in FINANCE_ROOT.rglob("*.py"):
        rel_parts = py.relative_to(FINANCE_ROOT).parts
        subpkg = rel_parts[0] if len(rel_parts) > 1 else ""
        if subpkg in ALLOWED_XLSX_DIRS:
            continue
        if _imports_openpyxl(py):
            offenders.append(str(py))
    assert not offenders, f"openpyxl вне importing/exporting: {offenders}"


def test_read_module_imports_only_kernel_db_and_models():
    """A6/§12: read/ — чистая проекция (kernel/db/models), без services/api/importing."""
    forbidden_substrings = (
        "app.contexts.finance.services",
        "app.contexts.finance.api",
        "app.contexts.finance.importing",
        "app.contexts.finance.exporting",
        "app.analytics",
        "app.delivery",
    )
    for py in (FINANCE_ROOT / "read").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for bad in forbidden_substrings:
            assert bad not in src, f"{py} импортирует {bad}"


def test_importing_not_referenced_outside_finance_package():
    """§12: finance.importing импортируется только внутри finance (изоляция ACL)."""
    project_root = Path("app")
    for py in project_root.rglob("*.py"):
        if FINANCE_ROOT in py.parents or py == FINANCE_ROOT:
            continue  # внутри finance — разрешено
        src = py.read_text(encoding="utf-8")
        assert "finance.importing" not in src, (
            f"{py} ссылается на finance.importing извне контекста"
        )
        assert "finance.exporting" not in src, (
            f"{py} ссылается на finance.exporting извне контекста"
        )
