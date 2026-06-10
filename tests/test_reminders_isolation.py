"""S3 Reminders — A7 (изоляция): домен не импортирует delivery; планировщик в одном
event-loop; import-linter зелёный.

Контракт границ закрепляет import-linter (контекст-независимость + слои), но этот
тест ловит нарушение статически по исходникам независимо от состояния pyproject
(как test_finance_isolation для ACL):
  - reminders НЕ импортирует app.delivery (standalone-шов, ADR-0004);
  - reminders НЕ импортирует app.analytics (ребро reminders→analytics запрещено, F2.1);
  - reminders НЕ зовёт aiogram/Bot напрямую — доставка только через инъектируемый
    Notifier-порт (единственный исходящий шов — Notifications, notifications I1);
  - reminders НЕ импортирует соседние контексты (mutual independence).
Своего loop/демона у домена нет: срабатывание — job-callable для generic-scheduler.
"""

from __future__ import annotations

import ast
from pathlib import Path

REM_ROOT = Path("app/contexts/reminders")

FORBIDDEN_PREFIXES = (
    "app.delivery",
    "app.analytics",
    "aiogram",
    "app.contexts.lists",
    "app.contexts.finance",
    "app.contexts.nutrition",
)


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module)
    return names


def test_reminders_does_not_import_forbidden_modules():
    offenders: list[str] = []
    for py in REM_ROOT.rglob("*.py"):
        for name in _imported_names(py):
            if any(name == p or name.startswith(p + ".") for p in FORBIDDEN_PREFIXES):
                offenders.append(f"{py}: {name}")
    assert not offenders, f"запрещённые импорты в reminders: {offenders}"


def test_reminders_has_no_own_event_loop():
    """Своего loop/демона нет: ни asyncio.run, ни create_task, ни new_event_loop
    в домене — срабатывание отдаётся generic-scheduler как job-callable (F2.1)."""
    banned = ("asyncio.run(", "asyncio.new_event_loop", "loop.run_forever", "create_task(")
    offenders: list[str] = []
    for py in REM_ROOT.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for token in banned:
            if token in src:
                offenders.append(f"{py}: {token}")
    assert not offenders, f"reminders держит собственный event-loop: {offenders}"


def test_delivery_goes_through_injected_notifier_only():
    """Домен не зовёт app.notifications напрямую и не строит Bot — доставка через порт."""
    offenders: list[str] = []
    for py in REM_ROOT.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        if "app.notifications" in src or "from aiogram" in src or "import aiogram" in src:
            offenders.append(str(py))
    assert not offenders, f"reminders зовёт транспорт напрямую (не через Notifier): {offenders}"
