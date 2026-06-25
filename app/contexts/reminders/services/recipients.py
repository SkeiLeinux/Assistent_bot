"""Резолв получателей push (ТЗ §6.3, I6/I10).

Адресат push определяет `delivery_target` (S2), он приоритетнее scope:
OWNER → владелец; FAMILY → оба члена; PARTNER → только второй член семьи.
NULL ⇒ выводится из scope (v0.3-поведение): PERSONAL → владелец, FAMILY → оба.
Резолв через kernel (FamilyRepository.members / UserRepository) — домен не лезет
в users-таблицу мимо kernel.
"""

from app.kernel.models import Scope, User
from app.kernel.repositories import FamilyRepository, UserRepository

from app.contexts.reminders.models import DeliveryTarget, Reminder


async def resolve_recipients(session, reminder: Reminder) -> list[User]:
    """Список User-получателей push (I6/I10). PARTNER → пусто, если партнёра нет
    (вызывающий логирует и пропускает доставку)."""
    target = reminder.delivery_target
    owner = await UserRepository(session).get(reminder.owner_id)

    async def _members() -> list[User]:
        rows = await FamilyRepository(session).members(reminder.family_id)
        return [user for _membership, user in rows]

    if target == DeliveryTarget.OWNER:
        return [owner] if owner is not None else []
    if target == DeliveryTarget.PARTNER:
        return [u for u in await _members() if u.id != reminder.owner_id]
    if target == DeliveryTarget.FAMILY:
        members = await _members()
        return members or ([owner] if owner is not None else [])

    # target is None → вывод из scope (поведение v0.3).
    if reminder.scope == Scope.FAMILY:
        members = await _members()
        if members:
            return members
    return [owner] if owner is not None else []
