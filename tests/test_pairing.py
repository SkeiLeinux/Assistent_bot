"""A1–A3 — пейринг: создание/вступление, гварды I3/I4/I5, состояния /me."""

import pytest

from app.kernel.models import User
from app.kernel.services import IdentityService, PairingService
from app.kernel.services.errors import (
    AlreadyInFamily,
    FamilyFull,
    InviteNotFound,
    InviteUnusable,
)


async def _user(session, tg: int, name: str) -> User:
    u = User(telegram_user_id=tg, first_name=name)
    session.add(u)
    await session.flush()
    return u


async def test_create_and_pair(session):  # A1
    u1 = await _user(session, 1, "A")
    u2 = await _user(session, 2, "B")
    ps = PairingService(session)
    family, invite = await ps.create_family(u1)
    joined = await ps.join(u2, invite.code)
    assert joined.id == family.id
    assert await ps.families.member_count(family.id) == 2


async def test_invite_single_use(session):  # A2 / I3
    u1 = await _user(session, 1, "A")
    u2 = await _user(session, 2, "B")
    u3 = await _user(session, 3, "C")
    ps = PairingService(session)
    _, invite = await ps.create_family(u1)
    await ps.join(u2, invite.code)
    with pytest.raises((InviteUnusable, FamilyFull)):
        await ps.join(u3, invite.code)


async def test_full_family_rejects_reissue(session):  # A2 / I4
    u1 = await _user(session, 1, "A")
    u2 = await _user(session, 2, "B")
    ps = PairingService(session)
    _, invite = await ps.create_family(u1)
    await ps.join(u2, invite.code)
    with pytest.raises(FamilyFull):
        await ps.reissue_invite(u1)


async def test_self_and_repeat_join_rejected(session):  # A2 / I5
    u1 = await _user(session, 1, "A")
    ps = PairingService(session)
    _, invite = await ps.create_family(u1)
    with pytest.raises(AlreadyInFamily):
        await ps.join(u1, invite.code)
    with pytest.raises(AlreadyInFamily):
        await ps.create_family(u1)


async def test_unknown_code(session):
    u1 = await _user(session, 1, "A")
    with pytest.raises(InviteNotFound):
        await PairingService(session).join(u1, "NOPE")


async def test_me_three_states(session):  # A3
    ids = IdentityService(session)
    ps = PairingService(session)
    u1 = await ids.get_or_create_user(1, "A")
    assert await ids.family_with_members(u1.id) is None  # без семьи
    family, invite = await ps.create_family(u1)
    res = await ids.family_with_members(u1.id)  # создатель
    assert res is not None and res[0].id == family.id and len(res[1]) == 1
    u2 = await ids.get_or_create_user(2, "B")
    await ps.join(u2, invite.code)
    res2 = await ids.family_with_members(u1.id)  # спарен
    assert len(res2[1]) == 2
