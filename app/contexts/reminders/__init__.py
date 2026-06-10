"""Reminders — напоминания + срабатывание (write-side, ТЗ spec-reminders.md).

generic-напоминания (текст + время/recurrence + владелец); `Reminder` = SSOT
расписания (состояние только на нём, без журнала — R3). Срабатывание — job-callable
`reminder_poll` для регистрации в generic-`scheduler/` (ADR-0003); доставка — через
инъектируемый шов Notifications (домен Telegram-агностичен, ADR-0004).

Публичная поверхность для delivery — api.register_routes и services.ReminderService;
job — services.make_reminder_poll. analytics/delivery домен НЕ импортирует (A7).
"""
