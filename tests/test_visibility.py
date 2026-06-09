"""A5 — инвариант видимости на харнессе (synthetic Shareable).

personal → только владелец; family → оба; подарок (family+hidden_from) → владелец
видит, исключённый нет. Чтения идут через ShareableRepository (I10).
"""

from app.kernel.models import Family, Membership, User
from app.kernel.models.shareable import Scope
from app.kernel.sharing import Viewer
from tests.shareable_harness import Gift, GiftRepository


async def _two_user_family(session):
    u1 = User(telegram_user_id=1, first_name="A")
    u2 = User(telegram_user_id=2, first_name="B")
    session.add_all([u1, u2])
    await session.flush()
    fam = Family(created_by=u1.id)
    session.add(fam)
    await session.flush()
    session.add_all([
        Membership(user_id=u1.id, family_id=fam.id),
        Membership(user_id=u2.id, family_id=fam.id),
    ])
    await session.flush()
    return u1, u2, fam


async def test_personal_visible_only_to_owner(session):
    u1, u2, fam = await _two_user_family(session)
    g = Gift(title="diary", owner_id=u1.id, family_id=fam.id, scope=Scope.PERSONAL)
    session.add(g)
    await session.commit()
    repo = GiftRepository(session)
    v1 = Viewer(id=u1.id, family_id=fam.id)
    v2 = Viewer(id=u2.id, family_id=fam.id)
    assert [x.id for x in await repo.list_visible(v1)] == [g.id]
    assert await repo.list_visible(v2) == []


async def test_family_visible_to_both(session):
    u1, u2, fam = await _two_user_family(session)
    g = Gift(title="groceries", owner_id=u1.id, family_id=fam.id, scope=Scope.FAMILY)
    session.add(g)
    await session.commit()
    repo = GiftRepository(session)
    v1 = Viewer(id=u1.id, family_id=fam.id)
    v2 = Viewer(id=u2.id, family_id=fam.id)
    assert await repo.get_visible(v1, g.id) is not None
    assert await repo.get_visible(v2, g.id) is not None


async def test_gift_hidden_from_excluded(session):
    u1, u2, fam = await _two_user_family(session)
    g = Gift(title="present", owner_id=u1.id, family_id=fam.id, scope=Scope.FAMILY)
    g.hidden_from = [u2]
    session.add(g)
    await session.commit()
    repo = GiftRepository(session)
    v1 = Viewer(id=u1.id, family_id=fam.id)
    v2 = Viewer(id=u2.id, family_id=fam.id)
    assert await repo.get_visible(v1, g.id) is not None  # владелец видит
    assert await repo.get_visible(v2, g.id) is None       # одаряемый исключён
