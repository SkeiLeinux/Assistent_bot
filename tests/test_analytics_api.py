"""S3 Analytics — REST §9: overview, digest/preview, digest/schedules (GET/PUT).

Хендлеры — транспортный шов: читают request['viewer']/request['session'] (как в
contexts.*.api). Драйвим хендлеры напрямую через лёгкий FakeRequest (без
pytest-aiohttp — он не в зависимостях; аутентификация/сессия — ответственность
миниапы, не Analytics). Покрывает A6 (REST настройки расписания) + S3/S4
(preview/overview).
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from aiohttp import web

from app.kernel.models import Family, Membership, User
from app.kernel.sharing import Viewer

from app.contexts.finance.models import ActualAmount, Category
from app.contexts.finance.models.enums import CategoryStatus
from app.contexts.nutrition.models import FoodEntry

from app.analytics.api import routes as api_routes
from app.analytics.schedule import default_schedules


class FakeRequest(dict):
    """Минимальный stand-in web.Request: dict-доступ + query/match_info/json()."""

    def __init__(self, session, viewer, *, query=None, match_info=None, body=None):
        super().__init__()
        self["session"] = session
        self["viewer"] = viewer
        self.query = query or {}
        self.match_info = match_info or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise json.JSONDecodeError("no body", "", 0)
        return self._body


def _read(resp: web.Response):
    return resp.status, json.loads(resp.text)


async def _setup(session):
    u1 = User(telegram_user_id=301, first_name="Катя", timezone="Europe/Amsterdam")
    u2 = User(telegram_user_id=302, first_name="Сережа", timezone="Europe/Amsterdam")
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
    session.add_all(default_schedules(family_id=fam.id))
    await session.flush()

    parent = Category(name="Едовое", family_id=fam.id, status=CategoryStatus.ACTIVE)
    session.add(parent)
    await session.flush()
    child = Category(name="Продукты", parent_id=parent.id, family_id=fam.id,
                     status=CategoryStatus.ACTIVE)
    session.add(child)
    await session.flush()
    session.add(ActualAmount(category_id=child.id, date=date(2026, 6, 10),
                             amount=Decimal("1500.00")))
    session.add(FoodEntry(user_id=u1.id, created_at=datetime(2026, 6, 10, 12, 0),
                          description="meal", kcal=600, protein=10, fat=10, carbs=10))
    await session.commit()
    return fam, Viewer(id=u1.id, family_id=fam.id)


async def test_get_schedules_returns_defaults(session):
    fam, viewer = await _setup(session)
    status, body = _read(await api_routes.get_schedules(FakeRequest(session, viewer)))
    assert status == 200
    cadences = {s["cadence"] for s in body}
    assert cadences == {"daily", "weekly"}
    daily = next(s for s in body if s["cadence"] == "daily")
    assert daily["time_of_day"] == "23:59"
    assert daily["enabled"] is True
    assert daily["last_sent_at"] is None


async def test_put_schedule_updates_time_and_enabled(session):
    fam, viewer = await _setup(session)
    req = FakeRequest(session, viewer, match_info={"cadence": "daily"},
                      body={"enabled": False, "time_of_day": "08:30"})
    status, body = _read(await api_routes.put_schedule(req))
    assert status == 200
    assert body["cadence"] == "daily"
    assert body["enabled"] is False
    assert body["time_of_day"] == "08:30"

    # Перечитываем — персистентно.
    _, listing = _read(await api_routes.get_schedules(FakeRequest(session, viewer)))
    daily = next(s for s in listing if s["cadence"] == "daily")
    assert daily["enabled"] is False
    assert daily["time_of_day"] == "08:30"


async def test_put_weekly_weekday(session):
    fam, viewer = await _setup(session)
    req = FakeRequest(session, viewer, match_info={"cadence": "weekly"},
                      body={"weekday": 0, "time_of_day": "20:00"})
    status, body = _read(await api_routes.put_schedule(req))
    assert status == 200
    assert body["weekday"] == 0
    assert body["time_of_day"] == "20:00"


async def test_put_bad_cadence_422(session):
    fam, viewer = await _setup(session)
    req = FakeRequest(session, viewer, match_info={"cadence": "monthly"},
                      body={"time_of_day": "08:30"})
    with pytest.raises(web.HTTPUnprocessableEntity):
        await api_routes.put_schedule(req)


async def test_put_bad_time_422(session):
    fam, viewer = await _setup(session)
    req = FakeRequest(session, viewer, match_info={"cadence": "daily"},
                      body={"time_of_day": "25:99"})
    with pytest.raises(web.HTTPUnprocessableEntity):
        await api_routes.put_schedule(req)


async def test_get_overview(session):
    fam, viewer = await _setup(session)
    req = FakeRequest(session, viewer, query={"from": "2026-06-01", "to": "2026-06-30"})
    status, body = _read(await api_routes.get_overview(req))
    assert status == 200
    assert body["range"]["from"] == "2026-06-01"
    assert body["finance"]["fact"] == 1500.0
    assert body["nutrition"]["kcal"] == 600.0
    assert "by_type" in body["lists"]


async def test_get_digest_preview(session):
    fam, viewer = await _setup(session)
    status, body = _read(await api_routes.get_digest_preview(FakeRequest(session, viewer)))
    assert status == 200
    assert "sections" in body
    assert "period" in body
    spheres = {s["sphere"] for s in body["sections"]}
    assert "finance" in spheres
    assert "nutrition" in spheres
