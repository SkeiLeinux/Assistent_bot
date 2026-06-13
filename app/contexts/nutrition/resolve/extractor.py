"""resolve/extractor.py — сегментация + идентификация одним LLM-вызовом (§6).

Один извлекатель, два входа (текст / фото) → `list[ExtractedItem]` (строгий JSON,
1 вызов). Заменяет прямой `_SYSTEM_PROMPT`-путь в `/analyze`: теперь модель не
только сегментирует ввод на продукты, но и заполняет поля идентификации
(brand/fat_pct/portion_g/barcode) и per100g (с этикетки → label_complete=true,
иначе оценка) для КАЖДОЙ позиции. Числа на этом шаге — оценка модели; точные
данные подставляет резолвер (OFF) на следующем.

Направление зависимости (ADR-0004, §2): resolve → services — извлекатель
переиспользует Groq-клиент и JSON-парсер из `services.llm_service` (тот же
провайдер/модель, контракт NutritionLLM сохраняется), но возвращает свой DTO
`ExtractedItem`, поэтому это отдельный компонент, а не метод NutritionLLM
(services НЕ импортирует resolve — цикла нет).
"""

import base64
import logging
from typing import Protocol, runtime_checkable

from groq import APIError, RateLimitError
from pydantic import ValidationError

from app.config import settings
from app.contexts.nutrition.resolve.ports import ExtractedItem, ExtractResult
from app.contexts.nutrition.services.llm_service import (
    LLMError,
    LLMRateLimitError,
    _extract_json,
    _get_client,
)

logger = logging.getLogger(__name__)

# Промпт извлечения — дословно по spec-nutrition-retrieval §6 (целевой).
_EXTRACT_PROMPT = (
    "Ты — парсер дневника питания. Раздели запрос (текст и/или фото) на ОТДЕЛЬНЫЕ "
    "продукты/блюда и для КАЖДОГО верни запись.\n"
    "Верни СТРОГО JSON-объект:\n"
    '{"items": [ {"name":"<родовое название без бренда, рус.>","brand":<строка|null>,'
    '"fat_pct":<число|null>,"portion_g":<число|null>,"barcode":<строка цифр|null>,'
    '"per100g":{"kcal":<ч>,"protein":<ч>,"fat":<ч>,"carbs":<ч>},'
    '"label_complete":<true|false>,"confidence":<0..1>}, ... ]}\n'
    "Правила:\n"
    "- Одна запись на каждый отдельный продукт/блюдо. Пример: «гречка 200, курица 150» → ДВЕ записи.\n"
    "- per100g — ВСЕГДА заполни, значения на 100 г: если на фото читается этикетка — "
    "точные значения с неё и label_complete=true; иначе — твоя оценка и label_complete=false.\n"
    "- name — без бренда и жирности («молоко питьевое»); brand — отдельно («Домик в деревне»); fat_pct — если указан.\n"
    "- portion_g — масса именно этой порции, если понятна из запроса; иначе null.\n"
    "- barcode — только если цифры штрихкода явно видны; иначе null.\n"
    "- Числа без единиц. Никакого текста вне JSON."
)


@runtime_checkable
class Extractor(Protocol):
    """Контракт извлекателя. FoodService зависит только от него (тесты инжектят фейк)."""

    async def extract(
        self,
        *,
        text: str | None = None,
        images: list[bytes] | None = None,
        caption: str = "",
        mime_type: str = "image/jpeg",
    ) -> list[ExtractedItem]: ...


class LLMExtractor:
    """Groq/Scout-реализация извлекателя (vision + text, строгий JSON, §6)."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.groq_model

    async def _complete(self, user_content) -> list[ExtractedItem]:
        try:
            response = await _get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _EXTRACT_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=1500,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(
                "Лимит запросов к модели исчерпан, попробуй позже"
            ) from e
        except APIError as e:
            logger.error(f"Groq API error (extract): {e}")
            raise LLMError("Сервис расчёта КБЖУ временно недоступен") from e

        raw = response.choices[0].message.content or ""
        data = _extract_json(raw)
        try:
            result = ExtractResult.model_validate(data)
        except ValidationError as e:
            logger.warning(f"extractor: invalid schema: {e}\nraw: {raw}")
            raise LLMError("Модель вернула некорректные данные") from e

        if not result.items:
            raise LLMError("Не удалось распознать продукты")
        return result.items

    async def extract(
        self,
        *,
        text: str | None = None,
        images: list[bytes] | None = None,
        caption: str = "",
        mime_type: str = "image/jpeg",
    ) -> list[ExtractedItem]:
        if images:
            parts: list[dict] = [
                {
                    "type": "text",
                    "text": caption.strip()
                    or "Определи продукты на фото и оцени их КБЖУ.",
                }
            ]
            for image_bytes in images[:5]:  # лимит Groq: ≤ 5 изображений (I6)
                b64 = base64.b64encode(image_bytes).decode()
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    }
                )
            return await self._complete(parts)
        return await self._complete(text or "")
