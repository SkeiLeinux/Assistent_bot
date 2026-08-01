"""FIX-2 (партия 1) — мультифото в фото-режиме дневника (REST /api/v1/nutrition/analyze).

Два уровня:
  - извлекатель собирает N снимков в ОДИН вызов, пока их не больше лимита модели,
    и режет на БАТЧИ, когда больше (ТЗ контракта §5.2) — сети нет, клиент фейковый;
  - роут analyze (type=photo) читает список images (dataURL/raw), режет до 5,
    обратно совместим с одиночным `image`, пустой ввод → 422 (InvalidInput).

Хендлер драйвим напрямую через лёгкий FakeRequest (как в test_analytics_api) —
аутентификация/сессия снаружи; извлекатель подменяем фейком за контрактом
`Extractor` (I7 после консолидации ТЗ §4.3).

**Что здесь изменилось в волне 1.** Прежний тест `..._caps_at_model_limit`
фиксировал молчаливую обрезку `images[:cap]` и читал порог из конфига. Обрезки
больше нет — она теряла ровно тот снимок, ради которого пользователь его прислал
(оборот упаковки с таблицей). Тест не удалён, а переписан на новое поведение:
тот же ввод «снимков больше лимита» теперь обязан дать батчи и не потерять
ничего. Порог по-прежнему читается из конфига — хардкод «5» здесь один раз уже
протух вместе с моделью (scout=5 → qwen3.6=3 → gpt-5.4-mini=5).
"""

import base64
import json
from types import SimpleNamespace

import pytest

from app.kernel import Viewer
from app.kernel.models import Family, Membership, User

from app.contexts.nutrition.api import routes as nutri_routes
from app.contexts.nutrition.resolve.extractor import LLMExtractor
from app.contexts.nutrition.resolve.ports import ExtractedItem, Macros, ResolvedNutrition
from app.contexts.nutrition.services import (
    FoodService,
    InvalidInput,
    NutritionItem,
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ── Уровень извлекателя: один вызов до лимита, батчи сверх лимита (§5.2) ──────

class _FakeChat:
    """Фейковый chat.completions.create: канонический ответ, запись всех вызовов."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None
        )


def _patch_llm(monkeypatch, content: str) -> _FakeChat:
    import app.contexts.nutrition.services.llm_service as llm_mod

    chat = _FakeChat(content)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat))
    )
    monkeypatch.setattr(llm_mod, "_get_client", lambda: client)
    return chat


def _one_item(name="x") -> str:
    return json.dumps({"items": [{
        "name": name, "portion_g": 100, "readout": "estimate",
        "per100g": {"kcal": 1, "protein": 1, "fat": 1, "carbs": 1},
    }]})


async def test_extractor_photo_one_call_n_image_parts(monkeypatch):
    chat = _patch_llm(monkeypatch, _one_item())

    await LLMExtractor().extract(images=[b"a", b"b", b"c"], caption="тарелка")

    assert len(chat.calls) == 1  # все снимки — одним вызовом
    parts = chat.calls[0]["messages"][1]["content"]
    text_parts = [p for p in parts if p["type"] == "text"]
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(text_parts) == 1 and len(image_parts) == 3
    assert text_parts[0]["text"] == "тарелка"


async def test_extractor_photo_over_limit_goes_to_batches(monkeypatch):
    """Снимков больше лимита модели → батчи, и ни один снимок не потерян (§5.2)."""
    from app.config import settings

    chat = _patch_llm(monkeypatch, _one_item())
    limit = settings.llm_max_images
    n = limit + 2

    await LLMExtractor().extract(images=[bytes([i]) for i in range(n)])

    assert len(chat.calls) == 2  # limit + остаток, а не «первые limit»
    sent = [
        p for call in chat.calls
        for p in call["messages"][1]["content"] if p["type"] == "image_url"
    ]
    assert len(sent) == n  # ничего не обрезано молча


# ── Уровень роута: парсинг images, cap, обратная совместимость, 0 → 422 ───────
# /analyze (type=photo) теперь идёт через извлекатель (ADR-0013), а не llm.analyze_photo:
# фиксируем, что роут довёл изображения до извлекателя (парсинг/cap/back-compat — в роуте).

class RecordingExtractor:
    """Фейк-извлекатель — фиксирует, что пришло в extract(...)."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_images = None
        self.last_mode = None

    async def extract(self, *, mode="diary", text=None, images=None, caption="",
                      mime_type="image/jpeg", dish_name=None, weight_g=None):
        self.calls += 1
        self.last_images = images
        self.last_mode = mode
        return [
            ExtractedItem(name="блюдо", portion_g=100, readout="estimate",
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
