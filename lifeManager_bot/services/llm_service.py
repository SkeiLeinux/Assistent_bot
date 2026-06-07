"""
services/llm_service.py — обёртка над LLM для расчёта КБЖУ.

Зачем отдельный сервис:
  Хендлеры и FoodService не должны знать, какой провайдер считает калории.
  Здесь спрятан Groq (модель llama-4-scout). Захочешь локальную модель
  (Ollama + Qwen2.5-VL на 4070) — меняешь только этот файл, контракт тот же.

Контракт:
  parse_text(text)              -> NutritionResult   (ручной ввод)
  analyze_photo(bytes, caption) -> NutritionResult   (фото + описание)

Обе возвращают СПИСОК позиций (NutritionItem), потому что одна строка ввода
или одна тарелка на фото могут содержать несколько продуктов, а в модели
FoodEntry одна позиция = одно описание + вес + КБЖУ.

Почему НЕ считаем regex'ом: локальной базы состава продуктов у нас нет,
поэтому сами числа КБЖУ может дать только модель — и для текста, и для фото.
"""

import base64
import json
import logging

from groq import AsyncGroq, APIError, RateLimitError
from pydantic import BaseModel, ValidationError

from config import settings

logger = logging.getLogger(__name__)


# ── Контракт данных ─────────────────────────────────────────────────────────

class NutritionItem(BaseModel):
    """Одна позиция: продукт/блюдо с КБЖУ уже для съеденной порции."""
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
    """Базовая ошибка LLM с понятным пользователю сообщением."""


class LLMNotConfiguredError(LLMError):
    """Не задан ключ API — модуль расчёта КБЖУ недоступен."""


class LLMRateLimitError(LLMError):
    """Превышен лимит запросов к модели (429) — стоит повторить позже."""


# ── Промпт ────────────────────────────────────────────────────────────────

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

# Промпт для анализа целого блюда по нескольким фото.
# Возвращает ровно одну позицию — суммарное КБЖУ для указанной порции.
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

# Промпт для совмещённого режима: текст с граммовками + фото этикеток.
# Модель читает напечатанные значения с этикеток и применяет граммовки из текста.
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


# ── Клиент (ленивый синглтон) ────────────────────────────────────────────────
# AsyncGroq держит внутри httpx-клиент. Создаём один на процесс, чтобы не
# плодить соединения на каждый запрос.

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
    """
    Достаёт JSON из ответа модели. В json-режиме контент уже чистый JSON,
    но на всякий случай срезаем ```-ограждения и берём внешние скобки.
    """
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
    def __init__(self, model: str | None = None):
        self.model = model or settings.groq_model

    async def _complete(self, user_content, system_prompt: str | None = None) -> NutritionResult:
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
            raise LLMRateLimitError("Лимит запросов к модели исчерпан, попробуй позже") from e
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
        self,
        description: str,
        name: str,
        weight_g: float,
    ) -> NutritionResult:
        """Текстовый состав блюда + название + вес → суммарное КБЖУ для одной порции."""
        text = (
            f'Блюдо: "{name}", вес порции: {weight_g} г. '
            f"Состав: {description}. "
            "Рассчитай суммарное КБЖУ для всей этой порции."
        )
        return await self._complete(text, system_prompt=_DISH_PROMPT)

    async def analyze_dish_photos(
        self,
        images_b64: list[str],
        name: str,
        weight_g: float,
    ) -> NutritionResult:
        """До 5 фото блюда + название + вес → суммарное КБЖУ для одной порции."""
        parts: list[dict] = [
            {
                "type": "text",
                "text": (
                    f'Блюдо: "{name}", вес порции: {weight_g} г. '
                    "Рассчитай суммарное КБЖУ для всей этой порции."
                ),
            }
        ]
        for b64 in images_b64[:5]:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        return await self._complete(parts, system_prompt=_DISH_PROMPT)

    async def analyze_dish_combined(
        self,
        text: str,
        images_b64: list[str],
        name: str,
        weight_g: float,
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
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        return await self._complete(parts, system_prompt=_COMBINED_PROMPT)
