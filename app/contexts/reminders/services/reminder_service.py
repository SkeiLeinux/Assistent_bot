"""ReminderService — CRUD + ack/snooze (ТЗ §7/§9).

Standalone-шов (ADR-0004): принимает резолвнутого kernel-`Viewer`, Telegram-агностичен.
Видимость композируется из kernel-клауз (I6/I10) в репозитории. Транзакцией владеет
вызывающий (REST-middleware коммитит) — здесь только flush.

Время: `next_fire_at` хранится в naive UTC (§12). Локальное `fire_at` при создании
конвертируется по `owner.timezone`. Часы инжектируются (`now_fn`) — тесты без sleep.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.time import utcnow_naive
from app.kernel import IdentityService, Scope, Viewer
from app.kernel.repositories import FamilyRepository

from app.contexts.reminders.models import (
    DeliveryTarget,
    Recurrence,
    Reminder,
    ReminderStatus,
)
from app.contexts.reminders.repositories import ReminderRepository
from app.contexts.reminders.services.errors import (
    FamilyRequired,
    InvalidInput,
    ReminderNotFound,
)

_UNSET = object()  # отличает «поле не передано» от «передано null» в PATCH


def _to_naive_utc(dt: datetime, tz_name: str) -> datetime:
    """Привести fire_at к naive UTC. Aware — конвертим как есть; naive — трактуем
    как локальное время owner.timezone (§12) и переводим в UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        local = dt.replace(tzinfo=ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        # Неизвестная зона — трактуем fire_at уже как UTC (без сдвига).
        return dt
    return local.astimezone(timezone.utc).replace(tzinfo=None)


class ReminderService:
    def __init__(self, session, *, now_fn=utcnow_naive) -> None:
        self.session = session
        self.reminders = ReminderRepository(session)
        self.identity = IdentityService(session)
        self._now_fn = now_fn

    def _now(self) -> datetime:
        return self._now_fn()

    # ── Чтения ───────────────────────────────────────────────────────────────

    async def list_reminders(
        self, viewer: Viewer, status: ReminderStatus | None = None
    ) -> list[Reminder]:
        return await self.reminders.list_visible(viewer, status)

    async def get_reminder(self, viewer: Viewer, reminder_id: int) -> Reminder:
        rem = await self.reminders.get_visible(viewer, reminder_id)
        if rem is None:
            raise ReminderNotFound("Напоминание не найдено")
        return rem

    # ── Создание ─────────────────────────────────────────────────────────────

    async def create_reminder(
        self,
        viewer: Viewer,
        *,
        text: str,
        fire_at: datetime,
        recurrence: Recurrence = Recurrence.NONE,
        interval_seconds: int | None = None,
        scope: Scope = Scope.PERSONAL,
        max_occurrences: int | None = None,
        until: datetime | None = None,
        delivery_target: DeliveryTarget | None = None,
    ) -> Reminder:
        if not text or not text.strip():
            raise InvalidInput("Пустой текст напоминания")
        if scope == Scope.FAMILY and viewer.family_id is None:
            raise FamilyRequired("FAMILY-напоминание требует семьи (/family)")
        if recurrence == Recurrence.INTERVAL and (
            not interval_seconds or interval_seconds <= 0
        ):
            raise InvalidInput("Для recurrence=interval нужен положительный interval_seconds")
        if recurrence != Recurrence.INTERVAL:
            interval_seconds = None

        # family_id: для PERSONAL без семьи кладём собственный «семейный» якорь
        # отсутствует — модель требует family_id NOT NULL. Берём family_id зрителя,
        # если есть; иначе PERSONAL допустим только при наличии семьи (kernel-модель).
        if viewer.family_id is None:
            raise FamilyRequired("Сначала создай семью или вступи в неё (/family)")

        tz_name = await self._owner_timezone(viewer.id)
        next_fire_at = _to_naive_utc(fire_at, tz_name)

        # S1: условие остановки — только при повторяемом.
        until_utc = None
        if recurrence == Recurrence.NONE:
            if max_occurrences is not None or until is not None:
                raise InvalidInput(
                    "max_occurrences/until допустимы только при повторяемом напоминании"
                )
        else:
            if max_occurrences is not None and max_occurrences < 1:
                raise InvalidInput("max_occurrences должен быть ≥ 1")
            if until is not None:
                until_utc = _to_naive_utc(until, tz_name)
                if until_utc <= next_fire_at:
                    raise InvalidInput("until должен быть позже первого срабатывания")
        # S2: delivery_target=PARTNER требует второго члена семьи (I10).
        if delivery_target == DeliveryTarget.PARTNER:
            await self._require_partner(viewer)

        rem = Reminder(
            owner_id=viewer.id,
            family_id=viewer.family_id,
            scope=scope,
            text=text.strip(),
            next_fire_at=next_fire_at,
            recurrence=recurrence,
            interval_seconds=interval_seconds,
            max_occurrences=max_occurrences,
            until=until_utc,
            delivery_target=delivery_target,
            status=ReminderStatus.ACTIVE,
        )
        return await self.reminders.create(rem)

    # ── Правка ───────────────────────────────────────────────────────────────

    async def update_reminder(
        self,
        viewer: Viewer,
        reminder_id: int,
        *,
        text: str | None = None,
        fire_at=_UNSET,
        recurrence: Recurrence | None = None,
        interval_seconds=_UNSET,
        scope: Scope | None = None,
        max_occurrences=_UNSET,
        until=_UNSET,
        delivery_target=_UNSET,
    ) -> Reminder:
        rem = await self.get_reminder(viewer, reminder_id)

        if text is not None and text.strip():
            rem.text = text.strip()
        if recurrence is not None:
            rem.recurrence = recurrence
        if interval_seconds is not _UNSET:
            rem.interval_seconds = interval_seconds
        if scope is not None:
            if scope == Scope.FAMILY and viewer.family_id is None:
                raise FamilyRequired("FAMILY-напоминание требует семьи")
            rem.scope = scope
        if fire_at is not _UNSET and fire_at is not None:
            tz_name = await self._owner_timezone(rem.owner_id)
            rem.next_fire_at = _to_naive_utc(fire_at, tz_name)
            # Правка времени реактивирует завершённое/пропущенное.
            if rem.status in (ReminderStatus.DONE, ReminderStatus.MISSED):
                rem.status = ReminderStatus.ACTIVE

        if max_occurrences is not _UNSET:
            rem.max_occurrences = max_occurrences
        if until is not _UNSET:
            rem.until = (
                _to_naive_utc(until, await self._owner_timezone(rem.owner_id))
                if until is not None
                else None
            )
        if delivery_target is not _UNSET:
            if delivery_target == DeliveryTarget.PARTNER:
                await self._require_partner(viewer)
            rem.delivery_target = delivery_target

        # Согласованность interval после правки recurrence.
        if rem.recurrence == Recurrence.INTERVAL and (
            not rem.interval_seconds or rem.interval_seconds <= 0
        ):
            raise InvalidInput("recurrence=interval требует положительный interval_seconds")
        if rem.recurrence != Recurrence.INTERVAL:
            rem.interval_seconds = None
        # S1: условие остановки только при повторяемом; иначе снимаем.
        if rem.recurrence == Recurrence.NONE:
            rem.max_occurrences = None
            rem.until = None
        elif rem.max_occurrences is not None and rem.max_occurrences < 1:
            raise InvalidInput("max_occurrences должен быть ≥ 1")

        await self.session.flush()
        return rem

    async def delete_reminder(self, viewer: Viewer, reminder_id: int) -> None:
        """DELETE → CANCELLED (§9). Запись не удаляется физически: status=CANCELLED."""
        rem = await self.get_reminder(viewer, reminder_id)
        rem.status = ReminderStatus.CANCELLED
        rem.next_fire_at = None
        await self.session.flush()

    # ── ack / snooze (§7) ────────────────────────────────────────────────────

    async def ack(self, viewer: Viewer, reminder_id: int) -> Reminder:
        """«✅ Выполнено»: одноразовое → DONE; повторяемое → подтверждает текущее
        срабатывание, расписание НЕ трогает (§7)."""
        rem = await self.get_reminder(viewer, reminder_id)
        if rem.recurrence == Recurrence.NONE:
            rem.status = ReminderStatus.DONE
            rem.next_fire_at = None
        # recurring: no-op для расписания (подтверждение текущего fire).
        await self.session.flush()
        return rem

    async def snooze(self, viewer: Viewer, reminder_id: int, minutes: int) -> Reminder:
        """«⏰ Отложить N»: next_fire_at = now + N, status=ACTIVE (§7).

        Снуз за пределы следующего штатного слота (recurring) → ближайшее из
        (snooze-время, штатный слот) (§11)."""
        if minutes is None or minutes <= 0:
            raise InvalidInput("minutes должен быть положительным")
        rem = await self.get_reminder(viewer, reminder_id)
        snoozed = self._now() + timedelta(minutes=minutes)
        if rem.recurrence != Recurrence.NONE and rem.next_fire_at is not None:
            snoozed = min(snoozed, rem.next_fire_at)
        rem.next_fire_at = snoozed
        rem.status = ReminderStatus.ACTIVE
        await self.session.flush()
        return rem

    # ── Внутреннее ───────────────────────────────────────────────────────────

    async def _owner_timezone(self, owner_id: int) -> str:
        user = await self.identity.users.get(owner_id)
        return getattr(user, "timezone", None) or "UTC"

    async def _require_partner(self, viewer: Viewer) -> None:
        """I10: delivery_target=PARTNER требует второго члена семьи."""
        if viewer.family_id is None:
            raise FamilyRequired("Нужна семья")
        members = await FamilyRepository(self.session).members(viewer.family_id)
        if not any(u.id != viewer.id for _m, u in members):
            raise InvalidInput("delivery_target=partner требует второго члена семьи")
