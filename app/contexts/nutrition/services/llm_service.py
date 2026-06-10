"""services/llm_service.py — провайдер-агностичная обёртка над LLM для КБЖУ (ТЗ §I6/I7).

Зачем отдельный сервис: FoodService и delivery не знают, какой провайдер считает
калории. Здесь спрятан Groq (модель llama-4-scout). Замена Groq→локальная модель
(Ollama + Qwen2.5-VL) меняет только этот файл — контракт `NutritionLLM` тот же (I7).

Контракт (`NutritionLLM`): все методы возвращают `NutritionResult` (список
`NutritionItem`), т.к. одна строка/тарелка может содержать несколько продуктов.
Невалидный ответ / 429 → понятная `LLMError` (I6). Тесты подменяют реализацию
фейком за этим интерфейсом — живых вызовов Groq из pytest нет (GAP-6).

Почему НЕ regex: локальной базы состава продуктов нет — числа КБЖУ даёт только
модель (и для текста, и для фото).
"""

import base64
import json
import logging
from typing import Protocol, runtime_checkable

from groq import APIError, AsyncGroq, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)


# ── Контракт данных ─────────────────────────────────────────────────────────

class NutritionItem(BaseModel):
    """Одна позиция: продукт/блюдо с КБЖУ уже для съеденной порции (абсолют, I1)."""

    description: str
    weight_g: float | None = None
    kcal: float
    protein: float
    fat: float
    carbs: float


class NutritionResult(BaseModel):
    """Обёртка-объект (JSON-режим требует объект на верхнем уровне, не массив)."""

    items: list[NutritionItem]


# ── Ошибки ──────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Базовая ошибка LLM с понятным пользователю сообщением (REST: 502)."""


class LLMNotConfiguredError(LLMError):
    """Не задан ключ API — модуль расчёта КБЖУ недоступен."""


class LLMRateLimitError(LLMError):
    """Превышен лимит запросов к модели (429) — стоит повторить позже."""


# ── Провайдер-агностичный интерфейс (I7) ─────────────────────────────────────

@runtime_checkable
class NutritionLLM(Protocol):
    """Контракт LLM-расчёта КБЖУ. FoodService зависит ТОЛЬКО от него (I7)."""

    async def parse_text(self, text: str) -> NutritionResult: ...

    async def analyze_photo(
        self, image_bytes: bytes, caption: str = "", mime_type: str = "image/jpeg"
    ) -> NutritionResult: ...

    async def analyze_dish_text(
        self, description: str, name: str, weight_g: float
    ) -> NutritionResult: ...

    async def analyze_dish_photos(
        self, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult: ...

    async def analyze_dish_combined(
        self, text: str, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult: ...


# ── Промпты ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Ты — нутрициолог-калькулятор. По описанию еды или фото оцени КБЖУ.\n"
    "Верни СТРОГО JSON-объект вида:\n"
    '{"items": [{"description": "...", "weight_g": <число|null>, '
    '"kcal": <число>, "protein": <число>, "fat": <число>, "carbs": <число>}]}\n'
    "Правила:\n"
    "- Одна позиция на каждый отдельный продукт/блюдо.\n"
    "- КБЖУ — ИТОГ для съеденной порции, НЕ на 100 г.\n"
    "  Если на этикетке значения на 100 г — умножь каждое на (вес_порции / 100).\n"
    "  Пример: белки 7.8 г/100 г, порция 125 г → белки = 7.8 × 1.25 = 9.75 г.\n"
    "- kcal — КИЛОКАЛОРИИ (ккал). Если на этикетке есть и кДж, и ккал — бери только ккал.\n"
    "  кДж и ккал различаются в ~4.18 раза: 496 кДж ≠ 496 ккал, это ≈ 119 ккал.\n"
    "- Если вес не указан — прикинь типичную порцию и проставь её в weight_g.\n"
    "- description — короткое название на русском.\n"
    "- Числа без единиц измерения. Никакого текста вне JSON."
)

# Анализ целого блюда по нескольким фото → ровно одна позиция (суммарное КБЖУ).
_DISH_PROMPT = (
    "Ты — нутрициолог-калькулятор. По одному или нескольким фото оцени КБЖУ ВСЕГО блюда.\n"
    "Верни СТРОГО JSON-объект вида:\n"
    '{"items": [{"description": "<название>", "weight_g": <число>, '
    '"kcal": <число>, "protein": <число>, "fat": <число>, "carbs": <число>}]}\n'
    "Правила:\n"
    "- Верни ровно ОДНУ позицию — итого для всей порции.\n"
    "- description — точно то название блюда, которое передано в запросе.\n"
    "- weight_g — точно тот вес порции, который передан в запросе.\n"
    "- КБЖУ — суммарно для всей порции, не на 100 г.\n"
    "- Числа без единиц измерения. Никакого текста вне JSON."
)

# Совмещённый режим: текст с граммовками + фото этикеток.
_COMBINED_PROMPT = (
    "Ты — нутрициолог-калькулятор. Тебе дан состав блюда с граммовками (текст) "
    "и фото этикеток ингредиентов (изображения).\n"
    "Для каждого ингредиента из текста найди его этикетку на фото, прочитай КБЖУ на 100 г "
    "и вычисли КБЖУ для указанной граммовки. Если этикетки нет — используй типичные значения.\n"
    "Сложи результаты всех ингредиентов в одну итоговую позицию.\n"
    "Верни СТРОГО JSON-объект вида:\n"
    '{"items": [{"description": "<название>", "weight_g": <число>, '
    '"kcal": <число>, "protein": <число>, "fat": <число>, "carbs": <число>}]}\n'
    "Правила:\n"
    "- Верни ровно ОДНУ позицию — итого для всей порции.\n"
    "- description — точно то название блюда, которое передано в запросе.\n"
    "- weight_g — точно тот вес порции, который передан в запросе.\n"
    "- КБЖУ — суммарно для всей порции, не на 100 г.\n"
    "- Числа без единиц измерения. Никакого текста вне JSON."
)


# ── Клиент Groq (ленивый синглтон) ───────────────────────────────────────────
# AsyncGroq держит httpx-клиент. Создаём один на процесс. Ленивость важна и для
# тестов: импорт модуля не трогает сеть/ключ — клиент рождается только при вызове.

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if not settings.groq_api_key:
        raise LLMNotConfiguredError(
            "Расчёт КБЖУ недоступен: не задан GROQ_API_KEY в .env"
        )
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


def _extract_json(content: str) -> dict:
    """Достаёт JSON из ответа модели (срезает ```-ограждения, берёт внешние скобки)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.lstrip("json").strip().strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("Модель вернула ответ не в формате JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError("Не удалось разобрать ответ модели") from e


class LLMService:
    """Groq-реализация контракта `NutritionLLM` (vision + text, строгий JSON)."""

    def __init__(self, model: str | None = None):
        self.model = model or settings.groq_model

    async def _complete(
        self, user_content, system_prompt: str | None = None
    ) -> NutritionResult:
        """Общий вызов чата для текста и фото. user_content — str или список частей."""
        try:
            response = await _get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=1024,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(
                "Лимит запросов к модели исчерпан, попробуй позже"
            ) from e
        except APIError as e:
            logger.error(f"Groq API error: {e}")
            raise LLMError("Сервис расчёта КБЖУ временно недоступен") from e

        raw = response.choices[0].message.content or ""
        data = _extract_json(raw)
        try:
            result = NutritionResult.model_validate(data)
        except ValidationError as e:
            logger.warning(f"LLM returned invalid schema: {e}\nraw: {raw}")
            raise LLMError("Модель вернула некорректные данные") from e

        if not result.items:
            raise LLMError("Не удалось распознать продукты")
        return result

    async def parse_text(self, text: str) -> NutritionResult:
        """Ручной ввод: 'гречка 200, куриная грудка 150' -> позиции с КБЖУ."""
        return await self._complete(text)

    async def analyze_photo(
        self, image_bytes: bytes, caption: str = "", mime_type: str = "image/jpeg"
    ) -> NutritionResult:
        """Фото блюда/упаковки (+ опц. описание) -> позиции с КБЖУ."""
        b64 = base64.b64encode(image_bytes).decode()
        parts = [
            {
                "type": "text",
                "text": caption.strip() or "Определи продукты на фото и оцени их КБЖУ.",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            },
        ]
        return await self._complete(parts)

    async def analyze_dish_text(
        self, description: str, name: str, weight_g: float
    ) -> NutritionResult:
        """Текстовый состав блюда + название + вес → суммарное КБЖУ одной порции."""
        text = (
            f'Блюдо: "{name}", вес порции: {weight_g} г. '
            f"Состав: {description}. "
            "Рассчитай суммарное КБЖУ для всей этой порции."
        )
        return await self._complete(text, system_prompt=_DISH_PROMPT)

    async def analyze_dish_photos(
        self, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult:
        """До 5 фото блюда + название + вес → суммарное КБЖУ одной порции."""
        parts: list[dict] = [
            {
                "type": "text",
                "text": (
                    f'Блюдо: "{name}", вес порции: {weight_g} г. '
                    "Рассчитай суммарное КБЖУ для всей этой порции."
                ),
            }
        ]
        for b64 in images_b64[:5]:  # лимит Groq: ≤ 5 изображений (I6)
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        return await self._complete(parts, system_prompt=_DISH_PROMPT)

    async def analyze_dish_combined(
        self, text: str, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult:
        """Текст с граммовками + фото этикеток → суммарное КБЖУ. Самый точный режим."""
        parts: list[dict] = [
            {
                "type": "text",
                "text": (
                    f'Блюдо: "{name}", вес порции: {weight_g} г.\n'
                    f"Состав с граммовками: {text}."
                ),
            }
        ]
        for b64 in images_b64[:5]:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        return await self._complete(parts, system_prompt=_COMBINED_PROMPT)
