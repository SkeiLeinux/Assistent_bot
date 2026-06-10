"""Analytics & Digest (read-side) — публичная граница (ТЗ spec-analytics §10).

Read-side слой поверх contexts.*.read: кросс-сферный обзор + ретро-дайджест.
Своя write-сущность одна — DigestSchedule (конфиг расписания). digest-job
ЭКСПОРТИРУЕТСЯ (make_digest_job/run_digest_job); регистрирует её в generic-scheduler
ИНТЕГРАТОР из app/main.py (аудит F2.1 — Analytics не зависит от Reminders).

Контракт import-linter: analytics → kernel, db, contexts.{lists,finance,nutrition}.read,
notifications, scheduler. Прямой импорт models/repositories/services доменов и
contexts.reminders запрещён (A5).
"""

from app.analytics.digest import compose_digest, render_text
from app.analytics.job import make_digest_job, run_digest_job
from app.analytics.read_composition import build_overview, whole_months_in_range
from app.analytics.schedule import (
    Cadence,
    DigestSchedule,
    default_schedules,
    is_due,
    period_bounds,
    seed_default_schedules,
)

__all__ = [
    # конфиг расписания (write-сущность)
    "DigestSchedule",
    "Cadence",
    "default_schedules",
    "seed_default_schedules",
    "is_due",
    "period_bounds",
    # read-композиция
    "build_overview",
    "whole_months_in_range",
    # дайджест
    "compose_digest",
    "render_text",
    # digest-job (регистрирует интегратор)
    "make_digest_job",
    "run_digest_job",
]
