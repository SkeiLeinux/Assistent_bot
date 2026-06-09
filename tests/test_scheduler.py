"""Пустой тик планировщика логируется и не падает (DoD Среза 0)."""

import asyncio
import logging

from app.scheduler import Scheduler


def test_empty_tick_logs(caplog):
    scheduler = Scheduler(interval_seconds=60.0)
    with caplog.at_level(logging.INFO):
        asyncio.run(scheduler.tick())
    assert any("scheduler tick" in r.getMessage() for r in caplog.records)
