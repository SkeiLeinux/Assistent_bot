"""Контракт слоёв (import-linter) как тест — ADR-0004, roadmap §6.

Гоняет lint-imports из текущего venv (как run_migrations ищет alembic).
Красный контракт = красный тест.
"""

import subprocess
import sys
from pathlib import Path


def test_import_contract_holds():
    exe = Path(sys.executable).parent / (
        "lint-imports.exe" if sys.platform == "win32" else "lint-imports"
    )
    assert exe.exists(), f"lint-imports не найден: {exe} (pip install import-linter)"
    result = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
