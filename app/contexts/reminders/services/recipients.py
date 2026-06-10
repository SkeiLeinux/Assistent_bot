"""Резолв получателей push по scope (ТЗ §6.3, I6).

PERSONAL → владелец; FAMILY → оба члена семьи. Резолв через kernel
(FamilyRepository.members / UserRepository) — домен не лезет в users-таблицу мимо kernel.
"""

from app.kernel.models import Scope, User
from app.kernel.repositories import FamilyRepository, UserRepository

from app.contexts.reminders.models import Reminder


async def resolve_recipients(session, reminder: Reminder) -> list[User]:
    """Список User-получателей push для напоминания по его scope (I6)."""
    if reminder.scope == Scope.FAMILY:
        families = FamilyRepository(session)
        members = await families.members(reminder.family_id)
        recipients = [user for _membership, user in members]
        if recipients:
            return recipients
        # Семья без members (вырожденный случай) — деградируем к владельцу.
    owner = await UserRepository(session).get(reminder.owner_id)
    return [owner] if owner is not None else []
