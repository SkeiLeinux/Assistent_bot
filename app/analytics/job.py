"""digest-job — callable для generic-планировщика (ТЗ §7, аудит F2.1).

На тике: найти все «созревшие» расписания (enabled, наступило время относительно
своего last_sent_at) → для каждой семьи композировать дайджест per-recipient →
доставить через Notifications → обновить last_sent_at расписания (без дублей, edge §11).

Analytics НЕ держит свой event-loop и НЕ зависит от Reminders: это просто job, которую
регистрирует ИНТЕГРАТОР в top-level scheduler из main.py (digest-job только
ЭКСПОРТИРУЕТСЯ отсюда). `deliver` инжектируется (DI), чтобы не связываться жёстко с
сигнатурой Notifications, которую достраивает delivery-agent в этой же волне:
  deliver: async (chat_ids: list[int], text: str) -> None
Интегратор оборачивает реальный notifications.deliver(bot, chat_ids, text); тесты —
мок со счётчиком.

Таймзона окна — владельца семьи (ТЗ §3.1, F6.4): берём timezone у User=Family.created_by.
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.models import Family, Membership, User
from app.kernel.sharing import Viewer

from app.analytics.digest import compose_digest, render_text
from app.analytics.schedule import DigestSchedule, is_due, period_bounds
from app.db.time import utcnow_naive

Deliver = Callable[[list[int], str], Awaitable[None]]


async def _owner_timezone(session: AsyncSession, family: Family) -> str:
    """Таймзона владельца семьи (Family.created_by) — окно дайджеста (F6.4)."""
    owner = await session.get(User, family.created_by)
    return owner.timezone if owner and owner.timezone else "Europe/Amsterdam"


async def _recipients(session: AsyncSession, family_id: int) -> list[User]:
    """Члены семьи как получатели (push обоим, I4)."""
    rows = await session.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.family_id == family_id)
    )
    return list(rows.scalars().all())


async def run_digest_job(
    session: AsyncSession,
    *,
    deliver: Deliver,
    now: datetime | None = None,
) -> None:
    """Один проход дайджест-джобы. `now` — наивный UTC (фейк-часы в тестах)."""
    moment = now or utcnow_naive()

    schedules = list(
        (await session.execute(select(DigestSchedule).where(DigestSchedule.enabled)))
        .scalars()
        .all()
    )

    # Кэш семей/таймзон/получателей в пределах тика.
    fam_cache: dict[int, Family | None] = {}
    tz_cache: dict[int, str] = {}
    rcpt_cache: dict[int, list[User]] = {}

    for schedule in schedules:
        fam = fam_cache.get(schedule.family_id)
        if schedule.family_id not in fam_cache:
            fam = await session.get(Family, schedule.family_id)
            fam_cache[schedule.family_id] = fam
        if fam is None:
            continue

        tz_name = tz_cache.get(schedule.family_id)
        if tz_name is None:
            tz_name = await _owner_timezone(session, fam)
            tz_cache[schedule.family_id] = tz_name

        if not is_due(schedule, moment, tz_name):
            continue

        date_from, date_to = period_bounds(schedule, moment, tz_name)

        recipients = rcpt_cache.get(schedule.family_id)
        if recipients is None:
            recipients = await _recipients(session, schedule.family_id)
            rcpt_cache[schedule.family_id] = recipients

        await _dispatch(session, schedule, recipients, date_from, date_to, tz_name, deliver)

        # Окно отстреляно — двигаем last_sent_at даже если дайджест был пуст
        # (иначе созревшее расписание молотит каждый тик; дублей нет, edge §11).
        schedule.last_sent_at = moment

    await session.flush()


async def _dispatch(
    session: AsyncSession,
    schedule: DigestSchedule,
    recipients: list[User],
    date_from,
    date_to,
    tz_name: str,
    deliver: Deliver,
) -> None:
    """Композировать per-recipient и доставить. Пустой дайджест — не шлём (ТЗ §11)."""
    rendered: list[tuple[int, str]] = []
    for user in recipients:
        viewer = Viewer(id=user.id, family_id=schedule.family_id)
        # Питание группируется в таймзоне самого пользователя (read), окно — владельца.
        user_tz = user.timezone or tz_name
        dto = await compose_digest(session, viewer, date_from, date_to, user_tz)
        if dto is None:
            continue  # этому получателю показывать нечего
        rendered.append((user.telegram_user_id, render_text(dto, user.first_name)))

    if not rendered:
        return

    # Один и тот же текст у каждого по своей композиции — шлём индивидуально, но если
    # все тексты идентичны (общие сферы), группируем получателей в один deliver-вызов.
    by_text: dict[str, list[int]] = {}
    for chat_id, text in rendered:
        by_text.setdefault(text, []).append(chat_id)
    for text, chat_ids in by_text.items():
        await deliver(chat_ids, text)


def make_digest_job(
    session_factory: Callable[[], AsyncSession],
    deliver: Deliver,
) -> Callable[[], Awaitable[None]]:
    """Фабрика job-callable для scheduler.register (зовёт ИНТЕГРАТОР в main.py).

    `session_factory` — async_session_maker миниапы; на каждый тик — своя сессия.
    """

    async def _job() -> None:
        async with session_factory() as session:
            await run_digest_job(session, deliver=deliver)
            await session.commit()

    return _job
