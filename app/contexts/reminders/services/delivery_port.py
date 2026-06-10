"""Шов доставки Reminders → Notifications (инъектируемый порт, ADR-0004 standalone).

Reminders НЕ импортирует Telegram/aiogram и НЕ зовёт notifications.deliver напрямую
из домена — он принимает `Notifier` (callable порт) снаружи (композиция в main.py).
Сигнатура порта = целевой интерфейс Notifications (ТЗ notifications §8):
deliver(recipients, text, *, buttons, kind). Конкретный адаптер к app/notifications/
строит интегратор; тесты подставляют мок-счётчик.

ВАЖНО (парковка Q-rem-1): текущий app/notifications/deliver(bot, chat_ids, text) НЕ
принимает buttons/recipients/kind — это S0-примитив. Кнопки ack/snooze (R1) и payload
`rem:ack:{id}`/`rem:snooze:{id}` (notifications §7) требуют расширения его API. До этого
доставку в тестах мокаем, прод-адаптер — за интегратором/delivery-agent.
"""

from dataclasses import dataclass
from typing import Protocol

from app.kernel.models import User


@dataclass(frozen=True)
class Button:
    """Инлайн-кнопка push: подпись + callback-payload (notifications §7)."""

    text: str
    callback_data: str


class Notifier(Protocol):
    """Порт доставки (целевой интерфейс Notifications §8). Реализация — снаружи."""

    async def deliver(
        self,
        recipients: list[User],
        text: str,
        *,
        buttons: list[Button] | None = None,
        kind: str,
    ) -> None: ...


def ack_button(reminder_id: int) -> Button:
    """✅ Выполнено → callback `rem:ack:{id}` (notifications §7, R1)."""
    return Button(text="✅ Выполнено", callback_data=f"rem:ack:{reminder_id}")


def snooze_button(reminder_id: int) -> Button:
    """⏰ Отложить 1 ч → callback `rem:snooze:{id}` (notifications §7, R1)."""
    return Button(text="⏰ Отложить 1 ч", callback_data=f"rem:snooze:{reminder_id}")


def reminder_buttons(reminder_id: int) -> list[Button]:
    """Обе кнопки push напоминания (R1): ack + snooze."""
    return [ack_button(reminder_id), snooze_button(reminder_id)]
