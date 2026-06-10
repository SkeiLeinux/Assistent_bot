"""Исходящий шов уведомлений (supporting-контекст, ТЗ §1/§9, ADR-0004).

Единственная исходящая точка в Telegram (I1): домены формируют ЧТО и КОМУ,
Notifications — КАК доставить (политика §5: лог → один ретрай → алерт админу).
Stateless (ТЗ §3): ни БД-сущности, ни очередей (frozen §5) — попытка + один
ретрай в общем event-loop.

Публичная поверхность:
  Notifier(bot)          — шов доставки; Bot инъектится снаружи (I5, композиция в main.py)
  Notifier.deliver(...)  — доставить с политикой §5 → DeliveryReport
  Notifier.alert_admin(text) / .alert_admin_exc(exc, ctx) — служебный алерт
  Button{text, callback_data}      — инлайн-кнопка push (CH-9)
  DeliveryReport{sent, blocked, failed}
  resolve_recipients(...)          — резолв получателей по scope/family (kernel)

Контракт import-linter: notifications → kernel, db (+ aiogram Bot через DI).
Контексты/delivery НЕ импортируются (supporting-слой ниже них).
"""

from app.notifications.models import Button, DeliveryReport
from app.notifications.recipients import resolve_recipients
from app.notifications.service import Notifier, render_keyboard, truncate

__all__ = [
    "Notifier",
    "Button",
    "DeliveryReport",
    "resolve_recipients",
    "render_keyboard",
    "truncate",
]
