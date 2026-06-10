"""Notifications.recipients — резолв получателей по scope/family (ТЗ §7/§8).

Notifications принимает уже резолвнутый список ``User`` (источник делает
``visible_to`` ДО вызова deliver — I2), но для удобства источников и change-хука
здесь даётся kernel-хелпер: scope/family → список ``User``.

Правила (ТЗ §7):
  PERSONAL → владелец;
  FAMILY   → оба члена семьи;
  change-уведомления → минус актор (своё же изменение не пушим),
                       минус исключённые hidden_from (I2 — скрытый подарок).

Импортирует только kernel (контракт слоёв: notifications ниже contexts/delivery).
"""

from collections.abc import Iterable

from app.kernel.models import User
from app.kernel.models.shareable import Scope
from app.kernel.services import IdentityService


async def resolve_recipients(
    session,
    *,
    scope: Scope,
    owner_id: int,
    family_id: int | None,
    exclude_actor: int | None = None,
    hidden_from: Iterable[int] | None = None,
) -> list[User]:
    """Получатели уведомления для (scope, владелец, семья).

    PERSONAL → только владелец; FAMILY → все члены семьи. Затем вычитаются
    актор (change-уведомления §7) и исключённые видимостью (hidden_from, I2).
    """
    excluded: set[int] = set(hidden_from or ())
    if exclude_actor is not None:
        excluded.add(exclude_actor)

    identity = IdentityService(session)

    if scope == Scope.PERSONAL:
        owner = await identity.users.get(owner_id)
        candidates = [owner] if owner is not None else []
    else:  # FAMILY
        if family_id is None:
            owner = await identity.users.get(owner_id)
            candidates = [owner] if owner is not None else []
        else:
            members = await identity.families.members(family_id)
            candidates = [user for _membership, user in members]

    return [u for u in candidates if u is not None and u.id not in excluded]
