"""FIX-2 (партия 1) — мультифото в фото-режиме дневника (REST /api/v1/nutrition/analyze).

Два уровня:
  - LLMService.analyze_photo собирает N image-частей в ОДИН вызов (cap = лимит модели) — промпт
    не меняется (монкипатчим _complete, сети нет).
  - роут analyze (type=photo) читает список images (dataURL/raw), режет до 5,
    обратно совместим с одиночным `image`, пустой ввод → 422 (InvalidInput).

Хендлер драйвим напрямую через лёгкий FakeRequest (как в test_analytics_api) —
аутентификация/сессия снаружи; LLM подменяем фейком за контрактом NutritionLLM.
"""

import base64
import json

import pytest

from app.kernel import Viewer
from app.kernel.models import Family, Membership, User

from app.contexts.nutrition.api import routes as nutri_routes
from app.contexts.nutrition.resolve.ports import ExtractedItem, Macros, ResolvedNutrition
from app.contexts.nutrition.services import (
    FoodService,
    InvalidInput,
    NutritionItem,
    NutritionResult,
)
from app.contexts.nutrition.services.llm_service import LLMService


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ── Уровень LLMService: один вызов, N image-частей, cap = лимит модели ────────

async def test_llm_analyze_photo_one_call_n_image_parts(monkeypatch):
    svc = LLMService()
    cap: dict = {"calls": 0, "content": None, "system": "sentinel"}

    async def fake_complete(user_content, system_prompt=None):
        cap["calls"] += 1
        cap["content"] = user_content
        cap["system"] = system_prompt
        return NutritionResult(
            items=[NutritionItem(description="x", weight_g=100, kcal=1, protein=1, fat=1, carbs=1)]
        )

    monkeypatch.setattr(svc, "_complete", fake_complete)

    await svc.analyze_photo([b"a", b"b", b"c"], caption="тарелка")

    assert cap["calls"] == 1  # все снимки — одним вызовом
    text_parts = [p for p in cap["content"] if p["type"] == "text"]
    image_parts = [p for p in cap["content"] if p["type"] == "image_url"]
    assert len(text_parts) == 1 and len(image_parts) == 3
    assert text_parts[0]["text"] == "тарелка"
    # промпт дневника не меняется → системный остаётся дефолтным (_SYSTEM_PROMPT)
    assert cap["system"] is None


async def test_llm_analyze_photo_caps_at_model_limit(monkeypatch):
    """Кап = лимит МОДЕЛИ (`settings.llm_max_images`), а не константа домена.

    Тест читает лимит из конфига намеренно: он переезжает при каждой смене
    провайдера (scout=5 → qwen3.6=3 → gpt-5.4-mini=5), и хардкод «5» здесь уже
    один раз протух вместе с моделью.
    """
    from app.config import settings

    svc = LLMService()
    cap: dict = {"content": None}

    async def fake_complete(user_content, system_prompt=None):
        cap["content"] = user_content
        return NutritionResult(
            items=[NutritionItem(description="x", weight_g=100, kcal=1, protein=1, fat=1, carbs=1)]
        )

    monkeypatch.setattr(svc, "_complete", fake_complete)

    limit = settings.llm_max_images
    await svc.analyze_photo([bytes([i]) for i in range(limit + 2)])

    image_parts = [p for p in cap["content"] if p["type"] == "image_url"]
    assert len(image_parts) == limit


# ── Уровень роута: парсинг images, cap, обратная совместимость, 0 → 422 ───────
# /analyze (type=photo) теперь идёт через извлекатель (ADR-0013), а не llm.analyze_photo:
# фиксируем, что роут довёл изображения до извлекателя (парсинг/cap/back-compat — в роуте).

class RecordingExtractor:
    """Фейк-извлекатель — фиксирует, что пришло в extract(images=...)."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_images = None

    async def extract(self, *, text=None, images=None, caption="", mime_type="image/jpeg"):
        self.calls += 1
        self.last_images = images
        return [
            ExtractedItem(name="блюдо", portion_g=100, label_complete=False,
                          per100g=Macros(kcal=1, protein=1, fat=1, carbs=1))
        ]


class _PassthroughResolver:
    total_budget_s = 12.0

    async def refine(self, item, *, allow_network=True):
        return ResolvedNutrition(per100g=item.per100g, source="estimate")


class FakeRequest(dict):
    """Минимальный stand-in web.Request: dict-доступ + json()."""

    def __init__(self, session, viewer, *, body=None):
        super().__init__()
        self["session"] = session
        self["viewer"] = viewer
        self.query = {}
        self.match_info = {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise json.JSONDecodeError("no body", "", 0)
        return self._body


def _inject(monkeypatch, session, rec: RecordingExtractor):
    monkeypatch.setattr(
        nutri_routes,
        "_service",
        lambda req: FoodService(
            req["session"], extractor=rec, resolver=_PassthroughResolver()
        ),
    )


async def test_route_analyze_photo_multi_single_call(session, monkeypatch):
    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    images = ["data:image/jpeg;base64," + _b64(b"a"), _b64(b"b"), _b64(b"c")]
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": images, "caption": "тарелка"})

    resp = await nutri_routes.analyze(req)

    assert resp.status == 200
    assert rec.calls == 1  # один вызов извлекателя
    assert len(rec.last_images) == 3 and all(isinstance(b, bytes) for b in rec.last_images)


async def test_route_analyze_photo_caps_at_five(session, monkeypatch):
    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    images = [_b64(bytes([i])) for i in range(6)]
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": images})

    await nutri_routes.analyze(req)

    assert len(rec.last_images) == 5  # бэкенд режет до 5


async def test_route_analyze_photo_empty_is_422(session, monkeypatch):
    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": []})

    with pytest.raises(InvalidInput):  # маппится в 422 общим middleware миниапы
        await nutri_routes.analyze(req)
    assert rec.calls == 0  # до извлекателя не дошли


async def test_route_analyze_photo_single_image_backward_compat(session, monkeypatch):
    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "image": "data:image/jpeg;base64," + _b64(b"x")})

    resp = await nutri_routes.analyze(req)

    assert resp.status == 200
    assert rec.calls == 1 and len(rec.last_images) == 1  # одиночное поле image работает


# ── Составное блюдо: /entries/composite + состав в /day ───────────────────────

async def _seed_viewer(session) -> Viewer:
    u = User(telegram_user_id=77, first_name="C", timezone="Europe/Amsterdam")
    session.add(u)
    await session.flush()
    fam = Family(created_by=u.id)
    session.add(fam)
    await session.flush()
    session.add(Membership(user_id=u.id, family_id=fam.id))
    await session.flush()
    return Viewer(id=u.id, family_id=fam.id)


def _inject_real(monkeypatch, session) -> None:
    """Composite/day не зовут извлекатель/резолвер — инжектим обычный FoodService."""
    monkeypatch.setattr(nutri_routes, "_service", lambda req: FoodService(req["session"]))


async def test_route_composite_returns_entry_with_ingredients(session, monkeypatch):
    _inject_real(monkeypatch, session)
    viewer = await _seed_viewer(session)
    req = FakeRequest(session, viewer, body={
        "name": "Паста болоньезе",
        "ingredients": [
            {"name": "паста", "w": 120, "kcal": 180, "p": 6, "f": 2, "c": 35, "source": "off"},
            {"name": "соус", "w": 90, "kcal": 210, "p": 9, "f": 14, "c": 8, "source": "estimate"},
        ],
        "meal_type": "lunch",
    })

    resp = await nutri_routes.post_entry_composite(req)

    assert resp.status == 200
    entry = json.loads(resp.body.decode())["entry"]
    assert entry["name"] == "Паста болоньезе"
    assert entry["w"] == 210 and entry["kcal"] == 390  # сумма строк
    assert [g["name"] for g in entry["ingredients"]] == ["паста", "соус"]
    assert entry["ingredients"][0]["source"] == "off"  # провенанс per-ingredient


async def test_route_composite_empty_is_422(session, monkeypatch):
    _inject_real(monkeypatch, session)
    viewer = await _seed_viewer(session)
    req = FakeRequest(session, viewer, body={"name": "X", "ingredients": []})

    with pytest.raises(InvalidInput):  # пустой состав → 422 общим middleware
        await nutri_routes.post_entry_composite(req)


async def test_route_composite_appears_in_day_with_composition(session, monkeypatch):
    _inject_real(monkeypatch, session)
    viewer = await _seed_viewer(session)
    await nutri_routes.post_entry_composite(FakeRequest(session, viewer, body={
        "name": "Боул",
        "ingredients": [
            {"name": "рис", "w": 150, "kcal": 200, "p": 4, "f": 1, "c": 45, "source": "off"},
            {"name": "курица", "w": 100, "kcal": 165, "p": 31, "f": 4, "c": 0, "source": "estimate"},
        ],
        "meal_type": "dinner",
    }))

    resp = await nutri_routes.get_day(FakeRequest(session, viewer))

    entries = json.loads(resp.body.decode())["entries"]
    composite = [e for e in entries if e.get("ingredients")]
    assert len(composite) == 1
    assert [g["name"] for g in composite[0]["ingredients"]] == ["рис", "курица"]


async def test_route_analyze_photo_returns_provenance(session, monkeypatch):
    """Контракт §10: каждая позиция /analyze несёт source (провенанс)."""
    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": [_b64(b"a")]})

    resp = await nutri_routes.analyze(req)
    payload = json.loads(resp.body.decode())
    item = payload["items"][0]
    assert item["source"] == "estimate"
    # опция C: per100g протащен на позицию (для пересчёта при правке веса)
    assert item["per100g"] == {"kcal": 1, "p": 1, "f": 1, "c": 1}
