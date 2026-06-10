"""Notifications — value-объекты публичного интерфейса (ТЗ §8, CH-9).

Stateless: ни одной БД-сущности (ТЗ §3). Это простые DTO между источниками и
шовом доставки — кнопка push и сводный отчёт доставки.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Button:
    """Инлайн-кнопка push (CH-9). Транспорт-агностична: text + callback_data.

    Источники (Reminders) формируют кнопки; рендер в aiogram-клавиатуру —
    внутри Notifications (service.render_keyboard). Payload маршрутизирует
    delivery/bot (ТЗ §7), напр. ``rem:ack:7`` / ``rem:snooze:7``.
    """

    text: str
    callback_data: str


@dataclass(frozen=True)
class DeliveryReport:
    """Сводка попытки доставки для лога/диагностики (ТЗ §8). Не персистится.

    sent    — доставлено успешно;
    blocked — бот заблокирован получателем (TelegramForbiddenError, без алерта);
    failed  — bad request / стойкий транзиент (с алертом по политике §5).
    """

    sent: int = 0
    blocked: int = 0
    failed: int = 0
