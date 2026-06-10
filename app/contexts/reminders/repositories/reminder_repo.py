"""Репозиторий напоминаний: видимые (visibility-фильтр) + выборка созревших.

Видимые чтения идут через kernel-клаузу `scope_visible(Reminder)` (I10/I6):
PERSONAL → владелец, FAMILY → семья зрителя. Выборка созревших (для job) —
служебная, БЕЗ фильтра видимости: планировщик системен, не привязан к зрителю.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer, scope_visible

from app.contexts.reminders.models import Reminder, ReminderStatus


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Чтения зрителя (visibility-фильтрованные) ────────────────────────────

    def visible_query(self, viewer: Viewer, status: ReminderStatus | None = None):
        q = select(Reminder).where(scope_visible(Reminder, viewer))
        if status is not None:
            q = q.where(Reminder.status == status)
        return q.order_by(Reminder.created_at)

    async def list_visible(
        self, viewer: Viewer, status: ReminderStatus | None = None
    ) -> list[Reminder]:
        result = await self.session.execute(self.visible_query(viewer, status))
        return list(result.scalars().all())

    async def get_visible(self, viewer: Viewer, reminder_id: int) -> Reminder | None:
        q = select(Reminder).where(
            scope_visible(Reminder, viewer), Reminder.id == reminder_id
        )
        result = await self.session.execute(q)
        return result.scalar_one_or_none()

    async def get(self, reminder_id: int) -> Reminder | None:
        return await self.session.get(Reminder, reminder_id)

    # ── Выборка созревших (служебная, для job; без фильтра зрителя) ───────────

    async def due(self, now: datetime) -> list[Reminder]:
        """ACTIVE с next_fire_at <= now (ТЗ §6.1). Порядок — по сроку (старые раньше)."""
        q = (
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.ACTIVE,
                Reminder.next_fire_at.is_not(None),
                Reminder.next_fire_at <= now,
            )
            .order_by(Reminder.next_fire_at)
        )
        result = await self.session.execute(q)
        return list(result.scalars().all())

    # ── Мутации ──────────────────────────────────────────────────────────────

    async def create(self, reminder: Reminder) -> Reminder:
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def delete(self, reminder: Reminder) -> None:
        await self.session.delete(reminder)
        await self.session.flush()
