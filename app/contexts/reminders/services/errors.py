"""Доменные ошибки Reminders. REST/общий middleware мапит их в коды."""


class ReminderError(Exception):
    """База ошибок контекста."""


class ReminderNotFound(ReminderError):
    """Напоминание не видно/не существует → 404 (скрывает существование)."""


class FamilyRequired(ReminderError):
    """Действие требует семьи (FAMILY-scope без семьи) → 403/409."""


class InvalidInput(ReminderError):
    """Невалидный пейлоад (recurrence/interval/время/snooze) → 422."""
