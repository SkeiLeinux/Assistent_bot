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

from pydantic import ValidationError

from app.config import settings
from app.contexts.nutrition.resolve.ports import ExtractedItem, ExtractResult
from app.contexts.nutrition.services.llm_service import (
    _API_ERRORS,
    _RATE_LIMIT_ERRORS,
    LLMError,
    LLMRateLimitError,
    LLMTarget,
    _cap_images,
    _extract_json,
    _get_client,
    make_client,
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
    """Groq-реализация извлекателя (vision + text, строгий JSON, §6).

    Модель — `settings.groq_model` (с 2026-07-25 `qwen/qwen3.6-27b`: Scout
    отключён Groq, см. ADR-0014).
    """

    def __init__(
        self, target: LLMTarget | None = None, model: str | None = None
    ) -> None:
        self._target = target
        self._client = make_client(target) if target is not None else None
        self.model = model or (target.model if target else settings.groq_model)
        # Наблюдаемость для раннера eval (ТЗ §6.2): сырой ответ и usage последнего
        # вызова. Прод их игнорирует — это только для замера цены/латентности.
        self.last_raw: str | None = None
        self.last_usage: dict | None = None

    def _client_for(self):
        return self._client if self._client is not None else _get_client()

    def _reasoning_effort(self) -> str:
        if self._target is not None:
            return self._target.reasoning_effort
        return settings.groq_reasoning_effort

    def _max_images(self) -> int:
        if self._target is not None:
            return self._target.max_images
        return settings.groq_max_images

    def _max_tokens_param(self) -> str:
        return self._target.max_tokens_param if self._target else "max_tokens"

    def _output_budget(self, default: int) -> int:
        if self._target is not None and self._target.max_output_tokens:
            return self._target.max_output_tokens
        return default

    def _temperature(self) -> float | None:
        return self._target.temperature if self._target is not None else 0.2

    def _send_response_format(self) -> bool:
        return self._target.send_response_format if self._target is not None else True

    async def _complete(self, user_content) -> list[ExtractedItem]:
        # См. llm_service._complete: у двухрежимных моделей без reasoning_effort
        # включается thinking и reasoning-токены выедают бюджет вывода → пустой JSON.
        # temperature=None → не слать (reasoning-модели OpenAI принимают только 1).
        # response_format — не для Anthropic (compat хочет json_schema).
        extra: dict = {self._max_tokens_param(): self._output_budget(1500)}
        if self._reasoning_effort():
            extra["reasoning_effort"] = self._reasoning_effort()
        if self._temperature() is not None:
            extra["temperature"] = self._temperature()
        if self._send_response_format():
            extra["response_format"] = {"type": "json_object"}
        try:
            response = await self._client_for().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _EXTRACT_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                **extra,
            )
        except _RATE_LIMIT_ERRORS as e:
            raise LLMRateLimitError(
                "Лимит запросов к модели исчерпан, попробуй позже"
            ) from e
        except _API_ERRORS as e:
            logger.error(f"LLM API error (extract): {e}")
            raise LLMError("Сервис расчёта КБЖУ временно недоступен") from e

        raw = response.choices[0].message.content or ""
        self.last_raw = raw
        usage = getattr(response, "usage", None)
        self.last_usage = usage.model_dump() if usage is not None else None
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
            for image_bytes in _cap_images(images, self._max_images()):  # кап модели
                b64 = base64.b64encode(image_bytes).decode()
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    }
                )
            return await self._complete(parts)
        return await self._complete(text or "")
