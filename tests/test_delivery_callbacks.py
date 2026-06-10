"""delivery/bot — generic callback-диспетчер (ТЗ §7 маршрутизация callback'ов).

A1 (маршрутизация): payload rem:ack:{id} / rem:snooze:{id} → вызов сервиса
Reminders. Edge §10: callback по закрытому/удалённому напоминанию → no-op +
короткий ответ. Диспетчер generic — новые префиксы добавляются таблицей.

Сервис Reminders зовётся как инъекция (контекст строится параллельно; шов —
таблица префиксов), здесь мокается.
"""

import pytest

from app.delivery.bot.callbacks import (
    CallbackDispatcher,
    CallbackResult,
    ReminderClosed,
    parse_callback,
)


# ── Парсинг payload ───────────────────────────────────────────────────────────


def test_parse_callback_ack():
    prefix, action, arg = parse_callback("rem:ack:7")
    assert (prefix, action, arg) == ("rem", "ack", "7")


def test_parse_callback_snooze():
    prefix, action, arg = parse_callback("rem:snooze:42")
    assert (prefix, action, arg) == ("rem", "snooze", "42")


def test_parse_callback_malformed_returns_none():
    assert parse_callback("garbage") is None
    assert parse_callback("") is None


# ── Фейковый сервис Reminders ─────────────────────────────────────────────────


class FakeReminderService:
    def __init__(self, closed_ids=()):
        self.acked: list[int] = []
        self.snoozed: list[tuple[int, int]] = []
        self._closed = set(closed_ids)

    async def ack(self, viewer, reminder_id: int):
        if reminder_id in self._closed:
            raise ReminderClosed(reminder_id)
        self.acked.append(reminder_id)

    async def snooze(self, viewer, reminder_id: int, minutes: int):
        if reminder_id in self._closed:
            raise ReminderClosed(reminder_id)
        self.snoozed.append((reminder_id, minutes))


def _dispatcher(svc):
    """Диспетчер с зарегистрированным reminders-обработчиком (как в main.py)."""
    d = CallbackDispatcher()

    async def on_ack(viewer, arg):
        await svc.ack(viewer, int(arg))
        return CallbackResult(answer="Отмечено выполненным")

    async def on_snooze(viewer, arg):
        await svc.snooze(viewer, int(arg), minutes=60)
        return CallbackResult(answer="Отложено на 1 ч")

    d.register("rem", {"ack": on_ack, "snooze": on_snooze})
    return d


# ── A1: маршрутизация ack/snooze ──────────────────────────────────────────────


async def test_dispatch_ack_calls_reminder_service():
    svc = FakeReminderService()
    d = _dispatcher(svc)
    viewer = object()

    result = await d.dispatch(viewer, "rem:ack:7")

    assert svc.acked == [7]
    assert result.answer == "Отмечено выполненным"


async def test_dispatch_snooze_calls_reminder_service():
    svc = FakeReminderService()
    d = _dispatcher(svc)

    result = await d.dispatch(object(), "rem:snooze:42")

    assert svc.snoozed == [(42, 60)]
    assert result.answer == "Отложено на 1 ч"


# ── Edge §10: closed/удалённое напоминание → no-op + короткий ответ ────────────


async def test_dispatch_ack_on_closed_is_noop_with_short_answer():
    svc = FakeReminderService(closed_ids={7})
    d = _dispatcher(svc)

    result = await d.dispatch(object(), "rem:ack:7")

    assert svc.acked == []  # no-op
    assert result.answer  # короткий ответ пользователю есть
    assert not result.alert  # без show_alert-шторма


async def test_dispatch_snooze_on_closed_is_noop():
    svc = FakeReminderService(closed_ids={42})
    d = _dispatcher(svc)

    result = await d.dispatch(object(), "rem:snooze:42")

    assert svc.snoozed == []
    assert result.answer


# ── Generic: неизвестный префикс/экшен ─────────────────────────────────────────


async def test_unknown_prefix_returns_neutral_result():
    svc = FakeReminderService()
    d = _dispatcher(svc)
    result = await d.dispatch(object(), "unknown:foo:1")
    assert result is not None
    assert svc.acked == [] and svc.snoozed == []


async def test_malformed_payload_returns_neutral_result():
    d = _dispatcher(FakeReminderService())
    result = await d.dispatch(object(), "junk")
    assert result is not None


async def test_new_prefix_added_by_table_without_touching_dispatcher():
    """Generic: добавление префикса — это register(), не правка диспетчера."""
    d = CallbackDispatcher()
    seen = []

    async def on_done(viewer, arg):
        seen.append(arg)
        return CallbackResult(answer="ok")

    d.register("lst", {"done": on_done})
    result = await d.dispatch(object(), "lst:done:99")

    assert seen == ["99"]
    assert result.answer == "ok"
