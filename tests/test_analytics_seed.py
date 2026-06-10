"""S3.1 wiring (F6.2) — сид дефолтных расписаний дайджеста при создании семьи.

Покрывает:
  - seed_default_schedules персистит 2 расписания (daily/weekly) в сессию;
  - kernel post_family (delivery) сидит расписания вместе с семьёй;
  - join НЕ сидит — семья уже существует со своими расписаниями.

delivery-точки драйвим напрямую (FakeRequest как test_analytics_api): аутентификация
и commit — ответственность миниапы, здесь проверяем именно сид.
"""

import json

from sqlalchemy import func, select

from app.kernel.models import Family, User
from app.kernel.services import PairingService

from app.analytics import seed_default_schedules
from app.analytics.schedule import Cadence, DigestSchedule

from app.delivery.miniapp_api import kernel_api


class FakeRequest(dict):
    def __init__(self, session, user, *, body=None):
        super().__init__()
        self["session"] = session
        self["user"] = user
        self._body = body

    async def json(self):
        if self._body is None:
            raise json.JSONDecodeError("no body", "", 0)
        return self._body


async def _count_schedules(session, family_id: int) -> int:
    res = await session.execute(
        select(func.count()).select_from(DigestSchedule).where(
            DigestSchedule.family_id == family_id
        )
    )
    return res.scalar_one()


async def _cadences(session, family_id: int) -> set:
    res = await session.execute(
        select(DigestSchedule.cadence).where(DigestSchedule.family_id == family_id)
    )
    return set(res.scalars().all())


# ── seed_default_schedules напрямую ──────────────────────────────────────────


async def test_seed_persists_two_schedules(session):
    u = User(telegram_user_id=701, first_name="Solo")
    session.add(u)
    await session.flush()
    fam = Family(created_by=u.id)
    session.add(fam)
    await session.flush()

    seed_default_schedules(session, fam.id)
    await session.flush()

    assert await _count_schedules(session, fam.id) == 2
    assert await _cadences(session, fam.id) == {Cadence.DAILY, Cadence.WEEKLY}


# ── интеграция: kernel post_family сидит ──────────────────────────────────────


async def test_post_family_seeds_schedules(session):
    u = User(telegram_user_id=702, first_name="Катя")
    session.add(u)
    await session.flush()

    req = FakeRequest(session, u, body={"name": "Наша"})
    resp = await kernel_api.post_family(req)
    await session.flush()
    assert resp.status == 201
    family_id = json.loads(resp.text)["family"]["id"]

    assert await _count_schedules(session, family_id) == 2
    assert await _cadences(session, family_id) == {Cadence.DAILY, Cadence.WEEKLY}


# ── join НЕ сидит (семья уже существует) ──────────────────────────────────────


async def test_join_does_not_seed(session):
    u1 = User(telegram_user_id=703, first_name="Создатель")
    u2 = User(telegram_user_id=704, first_name="Гость")
    session.add_all([u1, u2])
    await session.flush()

    ps = PairingService(session)
    family, invite = await ps.create_family(u1)
    seed_default_schedules(session, family.id)  # как делает post_family
    await session.flush()
    assert await _count_schedules(session, family.id) == 2

    # join не зовёт seed → счётчик не растёт.
    await ps.join(u2, invite.code)
    await session.flush()
    assert await _count_schedules(session, family.id) == 2
