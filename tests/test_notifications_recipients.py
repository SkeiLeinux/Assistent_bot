"""Notifications.recipients — резолв получателей по scope/family через kernel.

ТЗ §7: PERSONAL → владелец; FAMILY → оба. Change-уведомления: минус актор,
минус исключённые hidden_from (I2). Резолв опирается на kernel (family-члены).
"""

from app.kernel.models import Family, Membership, User
from app.kernel.models.shareable import Scope
from app.notifications.recipients import resolve_recipients


async def _family(session):
    u1 = User(telegram_user_id=101, first_name="A")
    u2 = User(telegram_user_id=102, first_name="B")
    session.add_all([u1, u2])
    await session.flush()
    fam = Family(created_by=u1.id)
    session.add(fam)
    await session.flush()
    session.add_all(
        [
            Membership(user_id=u1.id, family_id=fam.id),
            Membership(user_id=u2.id, family_id=fam.id),
        ]
    )
    await session.flush()
    return u1, u2, fam


async def test_personal_resolves_to_owner_only(session):
    u1, u2, fam = await _family(session)
    rec = await resolve_recipients(
        session, scope=Scope.PERSONAL, owner_id=u1.id, family_id=fam.id
    )
    assert [u.id for u in rec] == [u1.id]


async def test_family_resolves_to_both(session):
    u1, u2, fam = await _family(session)
    rec = await resolve_recipients(
        session, scope=Scope.FAMILY, owner_id=u1.id, family_id=fam.id
    )
    assert sorted(u.id for u in rec) == sorted([u1.id, u2.id])


async def test_family_minus_actor_for_change_notifications(session):
    """Change-уведомление: актор не получает своё же изменение (ТЗ §7)."""
    u1, u2, fam = await _family(session)
    rec = await resolve_recipients(
        session, scope=Scope.FAMILY, owner_id=u1.id, family_id=fam.id, exclude_actor=u1.id
    )
    assert [u.id for u in rec] == [u2.id]


async def test_family_minus_hidden_user(session):
    """Исключённый (hidden_from) не попадает в получателей (I2)."""
    u1, u2, fam = await _family(session)
    rec = await resolve_recipients(
        session,
        scope=Scope.FAMILY,
        owner_id=u1.id,
        family_id=fam.id,
        hidden_from=[u2.id],
    )
    assert [u.id for u in rec] == [u1.id]


async def test_personal_without_family_still_owner(session):
    """PERSONAL до пейринга — только владелец (family_id может отсутствовать)."""
    u1 = User(telegram_user_id=201, first_name="Solo")
    session.add(u1)
    await session.flush()
    rec = await resolve_recipients(
        session, scope=Scope.PERSONAL, owner_id=u1.id, family_id=None
    )
    assert [u.id for u in rec] == [u1.id]
