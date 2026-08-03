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

from tests.conftest import patch_llm_transport

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
    chat = _FakeChat(content)
    patch_llm_transport(monkeypatch, chat)
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


async def test_route_analyze_photo_passes_everything_under_the_cap(session, monkeypatch):
    """Кап роута читается из конфига и НИЧЕГО не режет, пока он не превышен.

    Заменяет прежний `..._caps_at_five`, который пинил молчаливую обрезку
    `images[:5]`. Обрезка убрана в блоке 15: она теряла ровно тот снимок, ради
    которого пользователь его прислал (оборот упаковки с таблицей), и делала
    это без единого слова — тот же дефект, который блок 6 уже убрал из
    извлекателя, а слой роутов сохранял.
    """
    from app.config import settings

    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    n = settings.api_max_images
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": [_b64(bytes([i])) for i in range(n)]})

    await nutri_routes.analyze(req)

    assert len(rec.last_images) == n


async def test_route_analyze_photo_over_cap_is_rejected_not_truncated(session, monkeypatch):
    """Сверх капа — внятный отказ, а не тихо усечённый ввод.

    Разница принципиальная: при обрезке пользователь получает КБЖУ, посчитанные
    НЕ по тому, что он прислал, и узнать об этом ему неоткуда. Отказ он видит
    сразу и решает сам.
    """
    from app.config import settings

    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    n = settings.api_max_images + 1
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "photo", "images": [_b64(bytes([i])) for i in range(n)]})

    with pytest.raises(InvalidInput) as exc:
        await nutri_routes.analyze(req)

    assert str(settings.api_max_images) in str(exc.value)  # текст называет предел
    assert rec.calls == 0  # до извлекателя не дошли, ничего не посчитано


async def test_route_analyze_photo_over_cap_rejected_in_dish_mode_too(session, monkeypatch):
    """Второе место того же хардкода — `/foods/analyze`.

    Литерал `[:5]` стоял в ДВУХ местах, и починка одного оставила бы второй:
    каталог принимал бы шестой снимок и молча его выбрасывал.
    """
    from app.config import settings

    rec = RecordingExtractor()
    _inject(monkeypatch, session, rec)
    n = settings.api_max_images + 1
    req = FakeRequest(session, Viewer(id=1, family_id=1), body={
        "name": "салат", "weight_g": 200,
        "images": [_b64(bytes([i])) for i in range(n)],
    })

    with pytest.raises(InvalidInput):
        await nutri_routes.post_food_analyze(req)
    assert rec.calls == 0


async def test_route_analyze_photo_reaches_provider_in_batches(session, monkeypatch):
    """**Боевой пробег батчей — впервые.**

    Механика §5.2 покрыта тестами извлекателя и раннером, но через REST не
    выполнялась ни разу: кап роута совпадал с лимитом провайдера, поэтому ввод
    физически не мог превысить лимит одного запроса. Этот тест идёт сквозь
    роут → извлекатель → фейковый транспорт и смотрит на СЧЁТЧИК ВЫЗОВОВ, а не
    на результат: результат сойдётся и при потерянном снимке.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_max_images", 5)
    monkeypatch.setattr(settings, "api_max_images", 10)
    chat = _patch_llm(monkeypatch, _one_item())
    monkeypatch.setattr(
        nutri_routes, "_service",
        lambda req: FoodService(req["session"], extractor=LLMExtractor(),
                                resolver=_PassthroughResolver()),
    )
    req = FakeRequest(session, Viewer(id=1, family_id=1), body={
        "type": "photo", "images": [_b64(bytes([i])) for i in range(8)],
    })

    resp = await nutri_routes.analyze(req)

    assert resp.status == 200
    assert len(chat.calls) == 2               # 5 + 3, а не один усечённый вызов
    sent = [
        p for call in chat.calls
        for p in call["messages"][1]["content"] if p["type"] == "image_url"
    ]
    assert len(sent) == 8                     # ни один снимок не потерян


async def test_route_returns_200_when_only_unresolved(session, monkeypatch):
    """DoD п.3: пустой `items` при непустом `unresolved` — это 200, а не ошибка.

    Сервисный уровень это уже держит (`test_all_unresolved_is_200_not_502`), но
    решает статус ответа РОУТ, и до фронта доходит именно он. Честный отказ
    модели («этикетку не видно») обязан приехать как частичный успех с
    объяснением: 502 сказал бы, что сломался сервис, чего не произошло.
    """
    class _OnlyUnreadable:
        calls = 0

        async def extract(self, **kwargs):
            return [ExtractedItem(name="соус", readout="unreadable",
                                  per100g=None, note="блик на этикетке")]

    class _NoNumbers:
        """Как прод-лестница на позиции без чисел (§6.6): `None`, а не нули."""

        total_budget_s = 12.0

        async def refine(self, item, *, allow_network=True):
            return None

    monkeypatch.setattr(
        nutri_routes, "_service",
        lambda req: FoodService(req["session"], extractor=_OnlyUnreadable(),
                                resolver=_NoNumbers()),
    )
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "text", "text": "соус"})

    resp = await nutri_routes.analyze(req)

    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["items"] == []
    assert len(payload["unresolved"]) == 1
    # Причина обязана доехать: без неё экран покажет «стало меньше позиций» и
    # ничего не объяснит — то есть честный отказ будет выглядеть как потеря данных.
    assert payload["unresolved"][0]["note"] == "блик на этикетке"
    assert payload["unresolved"][0]["reason"] == "unreadable"


async def test_route_serializes_alternative_when_label_disagrees(session, monkeypatch):
    """Вторая версия чисел (§7.2) доезжает до фронта.

    До блока 15 резолвер её считал, а `_to_item` терял — сверка label-preferred
    умирала на границе DTO, и пользователь не увидел ни одного расхождения,
    ради обнаружения которых ревизовался ADR-0013. Проверяем ОБА конца:
    масштабирование тем же множителем, что у основных чисел, и наличие per100g
    (иначе правка веса пересчитает основную версию и не тронет альтернативу).
    """
    from app.contexts.nutrition.resolve.ports import Macros as _M

    class _Disagreeing:
        total_budget_s = 12.0

        async def refine(self, item, *, allow_network=True):
            return ResolvedNutrition(
                per100g=_M(kcal=100, protein=10, fat=1, carbs=10),
                source="label",
                warnings=["label_disagrees_with_source"],
                alternative=ResolvedNutrition(
                    per100g=_M(kcal=200, protein=20, fat=2, carbs=20),
                    source="off", source_ref="4600000000000",
                    matched_name="Йогурт натуральный",
                ),
            )

    rec = RecordingExtractor()   # отдаёт одну позицию с portion_g=100
    monkeypatch.setattr(
        nutri_routes, "_service",
        lambda req: FoodService(req["session"], extractor=rec, resolver=_Disagreeing()),
    )
    req = FakeRequest(session, Viewer(id=1, family_id=1),
                      body={"type": "text", "text": "йогурт 100 г"})

    payload = json.loads((await nutri_routes.analyze(req)).text)

    item = payload["items"][0]
    assert item["kcal"] == 100 and item["source"] == "label"
    assert "label_disagrees_with_source" in item["warnings"]
    alt = item["alternative"]
    assert alt["kcal"] == 200 and alt["source"] == "off"
    assert alt["matched_name"] == "Йогурт натуральный"
    assert alt["per100g"]["kcal"] == 200


def test_route_cap_and_provider_cap_are_separate_settings():
    """Два разных предела, и схлопывать их в одно поле нельзя.

    `api_max_images` — сколько снимков примет API; `llm_max_images` — сколько
    уходит в ОДИН запрос к провайдеру. Батчи существуют ровно из-за их разности,
    поэтому равенство этих чисел означает, что батчи снова недостижимы. Второе
    двигается при смене модели (scout=5 → qwen3.6=3 → gpt-5.4-mini=5), первое —
    нет.
    """
    from app.config import Settings

    defaults = Settings.model_fields
    assert defaults["api_max_images"].default > defaults["llm_max_images"].default


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
