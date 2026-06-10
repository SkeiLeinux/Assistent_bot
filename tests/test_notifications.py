"""Notifications — критерии приёмки A1–A5 (ТЗ spec-notifications §12).

Фаза 1 (build-contract §2): тесты по A-критериям ДО кода. Транспорт Telegram —
фейковый Bot (фейк aiogram-исключений), живых вызовов нет.

Маппинг A→тест:
  A1 (push + кнопки)        → test_deliver_renders_buttons_*, test_deliver_success_report
  A2 (политика)             → test_forbidden_*, test_bad_request_*, test_transient_*
  A3 (видимость)            → см. tests/test_delivery_changes.py (хук Lists, I2)
  A4 (единственный шов)     → test_architecture / lint-imports (контракт слоёв)
  A5 (алерт не зацикливается)→ test_alert_admin_*
"""

import logging

import pytest
from aiogram.exceptions import (  # noqa: I001
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardMarkup

from app.kernel.models import User
from app.notifications import Button, DeliveryReport, Notifier


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """Убрать реальную задержку ретрая — тесты политики не ждут секунды."""
    from app.notifications import service

    monkeypatch.setattr(service, "RETRY_DELAY_SECONDS", 0, raising=True)


# ── Фейковый транспорт ────────────────────────────────────────────────────────


class FakeBot:
    """Минимальный фейк aiogram.Bot: пишет вызовы, по сценарию бросает ошибки.

    `script` — отображение chat_id → список «исходов» по попыткам (по индексу).
    Исход: None (успех) либо экземпляр Telegram*Error для имитации фейла.
    Если для chat_id исходов меньше, чем попыток — берётся последний (повтор).
    """

    def __init__(self, script: dict[int, list] | None = None) -> None:
        self.script = script or {}
        self.calls: list[dict] = []
        self._attempts: dict[int, int] = {}

    async def send_message(self, *, chat_id, text, reply_markup=None, **kwargs):
        self.calls.append(
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup, **kwargs}
        )
        outcomes = self.script.get(chat_id, [None])
        i = self._attempts.get(chat_id, 0)
        self._attempts[chat_id] = i + 1
        outcome = outcomes[i] if i < len(outcomes) else outcomes[-1]
        if outcome is not None:
            raise outcome
        return object()

    def calls_to(self, chat_id: int) -> int:
        return sum(1 for c in self.calls if c["chat_id"] == chat_id)


def _user(uid: int, tg: int) -> User:
    u = User(telegram_user_id=tg, first_name=f"U{uid}")
    u.id = uid
    return u


def _forbidden(msg="bot was blocked by the user"):
    return TelegramForbiddenError(method=None, message=msg)


def _bad_request(msg="message text is empty"):
    return TelegramBadRequest(method=None, message=msg)


def _network(msg="connection reset"):
    return TelegramNetworkError(method=None, message=msg)


def _server(msg="bad gateway"):
    return TelegramServerError(method=None, message=msg)


# ── A1: успех + кнопки ────────────────────────────────────────────────────────


async def test_deliver_success_report():
    bot = FakeBot()
    notifier = Notifier(bot)
    recipients = [_user(1, 111), _user(2, 222)]

    report = await notifier.deliver(recipients, "привет", kind="reminder")

    assert isinstance(report, DeliveryReport)
    assert report == DeliveryReport(sent=2, blocked=0, failed=0)
    # доставка идёт на telegram_user_id, не на внутренний User.id
    assert {c["chat_id"] for c in bot.calls} == {111, 222}


async def test_deliver_renders_buttons_into_inline_keyboard():
    bot = FakeBot()
    notifier = Notifier(bot)
    buttons = [
        Button(text="✅ Выполнено", callback_data="rem:ack:7"),
        Button(text="⏰ Отложить 1 ч", callback_data="rem:snooze:7"),
    ]

    await notifier.deliver([_user(1, 111)], "Витамин D", buttons=buttons, kind="reminder")

    markup = bot.calls[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    flat = [b for row in markup.inline_keyboard for b in row]
    assert [(b.text, b.callback_data) for b in flat] == [
        ("✅ Выполнено", "rem:ack:7"),
        ("⏰ Отложить 1 ч", "rem:snooze:7"),
    ]


async def test_deliver_without_buttons_sends_no_markup():
    bot = FakeBot()
    notifier = Notifier(bot)
    await notifier.deliver([_user(1, 111)], "дайджест", kind="digest")
    assert bot.calls[0]["reply_markup"] is None


# ── A2: политика недоставки §5 ────────────────────────────────────────────────


async def test_forbidden_is_logged_without_retry_or_alert(caplog):
    """Блокировка бота → лог(warning), без ретрая, без алерта (I4)."""
    bot = FakeBot(script={111: [_forbidden()]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    with caplog.at_level(logging.WARNING):
        report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=0, blocked=1, failed=0)
    assert bot.calls_to(111) == 1  # без ретрая
    assert alerts == []  # без алерта
    assert any("заблокир" in r.message.lower() or "block" in r.message.lower()
               for r in caplog.records)


async def test_bad_request_logged_no_retry_with_alert():
    """Bad request → лог, без ретрая; алерт админу (неожиданно)."""
    bot = FakeBot(script={111: [_bad_request()]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=0, blocked=0, failed=1)
    assert bot.calls_to(111) == 1  # без ретрая
    assert len(alerts) == 1  # неожиданная ошибка → алерт


async def test_transient_one_retry_then_success():
    """Транзиент → один ретрай; ретрай удался → sent, без алерта."""
    bot = FakeBot(script={111: [_network(), None]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=1, blocked=0, failed=0)
    assert bot.calls_to(111) == 2  # попытка + один ретрай
    assert alerts == []


async def test_transient_persistent_one_retry_then_alert():
    """Транзиент стойкий → один ретрай → всё ещё фейл → алерт админу."""
    bot = FakeBot(script={111: [_server(), _server()]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=0, blocked=0, failed=1)
    assert bot.calls_to(111) == 2  # ровно один ретрай, не больше
    assert len(alerts) == 1


async def test_unknown_exception_one_retry_then_alert():
    """Неклассифицированная ошибка трактуется как транзиент (ретрай + алерт)."""
    bot = FakeBot(script={111: [RuntimeError("boom"), RuntimeError("boom")]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=0, blocked=0, failed=1)
    assert bot.calls_to(111) == 2
    assert len(alerts) == 1


# ── A2: мульти-получатель, один фейл не прерывает остальных ────────────────────


async def test_multi_recipient_one_failure_does_not_stop_others():
    """Один партнёр заблокировал бота → второму доставлено (§5, edge §10)."""
    bot = FakeBot(script={111: [_forbidden()], 222: [None]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver(
        [_user(1, 111), _user(2, 222)], "общая задача", kind="reminder"
    )

    assert report == DeliveryReport(sent=1, blocked=1, failed=0)
    assert bot.calls_to(222) == 1  # второму доставлено
    assert alerts == []  # блокировка не алертится


async def test_multi_recipient_transient_alert_does_not_abort_loop():
    """Стойкий фейл одному (алерт) не мешает доставке другому."""
    bot = FakeBot(script={111: [_server(), _server()], 222: [None]})
    alerts: list[str] = []
    notifier = Notifier(bot, alert_sink=alerts.append)

    report = await notifier.deliver(
        [_user(1, 111), _user(2, 222)], "x", kind="reminder"
    )

    assert report == DeliveryReport(sent=1, blocked=0, failed=1)
    assert bot.calls_to(222) == 1
    assert len(alerts) == 1


# ── A5: алерт админу, без зацикливания ────────────────────────────────────────


async def test_alert_admin_sends_to_admin_id(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_telegram_id", 999, raising=False)
    bot = FakeBot()
    notifier = Notifier(bot)

    await notifier.alert_admin("стойкий фейл доставки")

    assert bot.calls[-1]["chat_id"] == 999
    assert "стойкий фейл доставки" in bot.calls[-1]["text"]


async def test_alert_admin_noop_when_admin_unset(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_telegram_id", None, raising=False)
    bot = FakeBot()
    notifier = Notifier(bot)

    await notifier.alert_admin("без админа")

    assert bot.calls == []  # никуда не шлём


async def test_alert_admin_failure_does_not_raise_or_loop(monkeypatch, caplog):
    """Сам алерт не ушёл → только лог, без исключения и без повторного алерта (I4)."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_telegram_id", 999, raising=False)
    bot = FakeBot(script={999: [_network(), _network(), _network()]})
    notifier = Notifier(bot)

    with caplog.at_level(logging.ERROR):
        await notifier.alert_admin("текст")  # не должно бросить

    # ровно одна попытка достучаться до админа — без ретрая/рекурсии алерта
    assert bot.calls_to(999) == 1


async def test_persistent_transient_alert_failure_no_infinite_loop(monkeypatch):
    """deliver-фейл шлёт alert_admin; если и тот не уходит — цикла нет (I4)."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_telegram_id", 999, raising=False)
    # 111: получатель стойко фейлит; 999: админ тоже фейлит
    bot = FakeBot(script={111: [_server(), _server()], 999: [_server()]})
    notifier = Notifier(bot)

    report = await notifier.deliver([_user(1, 111)], "x", kind="reminder")

    assert report == DeliveryReport(sent=0, blocked=0, failed=1)
    # одна попытка получателю-ретрай (2) + одна попытка админу (1) — и всё
    assert bot.calls_to(999) == 1


# ── §10: усечение длинного сообщения (>4096) ──────────────────────────────────


async def test_long_message_truncated_single_send():
    """Сообщение >4096 → усечение, одно сообщение (не разбиваем, Q1-А5)."""
    bot = FakeBot()
    notifier = Notifier(bot)
    long_text = "\n".join(f"строка {i}" for i in range(2000))

    report = await notifier.deliver([_user(1, 111)], long_text, kind="digest")

    assert report.sent == 1
    assert bot.calls_to(111) == 1  # одно сообщение, без разбивки
    assert len(bot.calls[0]["text"]) <= 4096
