"""Generic-планировщик: один тик каждые N секунд + реестр job (ADR-0003, аудит F2.1).

В Срезе 0 реестр пустой — пустой тик логируется. Реальные job (reminder_poll, digest)
регистрируются в app/main.py на Срезе 3. Знает только asyncio — без зависимостей
от доменов (контракт import-linter: scheduler → kernel + asyncio).
"""

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class Scheduler:
    def __init__(self, interval_seconds: float = 60.0) -> None:
        self.interval_seconds = interval_seconds
        self._jobs: list[Job] = []
        self._task: asyncio.Task | None = None

    def register(self, job: Job) -> None:
        """Зарегистрировать job. В S0 не вызывается — реестр пуст."""
        self._jobs.append(job)

    async def tick(self) -> None:
        """Один проход реестра. Ошибка отдельной job не валит планировщик."""
        logger.info("scheduler tick: %d job(s)", len(self._jobs))
        for job in self._jobs:
            try:
                await job()
            except Exception:  # noqa: BLE001 — изоляция job друг от друга
                logger.exception("scheduler job failed: %r", job)

    async def run(self) -> None:
        """Бесконечный цикл тика. Останавливается отменой задачи."""
        logger.info(
            "Планировщик запущен (интервал %.0f c, job: %d)",
            self.interval_seconds, len(self._jobs),
        )
        try:
            while True:
                await self.tick()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            logger.info("Планировщик остановлен")
            raise

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
