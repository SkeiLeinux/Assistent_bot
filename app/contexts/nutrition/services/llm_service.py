"""services/llm_service.py — провайдер-агностичная обёртка над LLM для КБЖУ (ТЗ §I6/I7).

Зачем отдельный сервис: FoodService и delivery не знают, какой провайдер считает
калории. Здесь спрятан Groq (модель — `settings.groq_model`). Замена Groq→локальная
модель (Ollama + Qwen2.5-VL) меняет только этот файл — контракт `NutritionLLM` тот
же (I7).

Оговорка про I7 (2026-07-25): агностичность заявлена на уровне КОНТРАКТА, но клиент
здесь жёстко `AsyncGroq`, а параметры вызова (лимит изображений, `reasoning_effort`)
— свойства конкретной модели. Смена провайдера сегодня = правка этого файла, а не
конфига. Закрывается ADR-0014 (переход на OpenAI-совместимый клиент с
конфигурируемым base_url + расщепление text/vision-моделей).

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
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from groq import APIError, AsyncGroq, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

# Прод ходит в Groq через AsyncGroq. Раннер eval гоняет другие OpenAI-совместимые
# провайдеры (OpenRouter/OpenAI) — для них groq-SDK не годится: он хардкодит путь
# `/openai/v1/chat/completions` и игнорирует версию в base_url (см. ADR-0017 §14.1).
# Поэтому не-Groq цели обслуживает openai-SDK (уважает base_url с версией). openai —
# опциональная зависимость: прод её не требует, только раннер. Отсюда — исключения
# ловим кортежем обеих SDK, если вторая доступна.
try:
    from openai import APIError as _OpenAIAPIError
    from openai import AsyncOpenAI as _AsyncOpenAI
    from openai import RateLimitError as _OpenAIRateLimitError

    _RATE_LIMIT_ERRORS: tuple = (RateLimitError, _OpenAIRateLimitError)
    _API_ERRORS: tuple = (APIError, _OpenAIAPIError)
except ImportError:  # прод-окружение без openai — работает только Groq-путь
    _AsyncOpenAI = None
    _RATE_LIMIT_ERRORS = (RateLimitError,)
    _API_ERRORS = (APIError,)


# ── Контракт данных ─────────────────────────────────────────────────────────

class NutritionItem(BaseModel):
    """Одна позиция: продукт/блюдо с КБЖУ уже для съеденной порции (абсолют, I1).

    Провенанс (ADR-0013, §5): `source`/`source_ref` происходят от резолвера и
    сохраняются в FoodEntry. `matched_name` — только транспорт analyze→confirm
    (как назван матч в источнике, для доверия/UI); в БД не пишется. `source=None`
    на «голой» позиции (ручной ввод) → save_entries подставит дефолт вызова.
    """

    description: str
    weight_g: float | None = None
    kcal: float
    protein: float
    fat: float
    carbs: float
    source: str | None = None        # провенанс: label/off/web/estimate/db/manual
    source_ref: str | None = None    # URL/код товара/цитата (off/web)
    matched_name: str | None = None  # как назван матч в источнике (не персистится)
    # per100g — транспорт для confirm-экрана (пересчёт КБЖУ при правке веса, ADR-0013
    # опция C). НЕ персистится (FoodEntry хранит абсолют за порцию, I1).
    kcal_100g: float | None = None
    protein_100g: float | None = None
    fat_100g: float | None = None
    carbs_100g: float | None = None


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
        self, images: list[bytes], caption: str = "", mime_type: str = "image/jpeg"
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


# ── Провайдер-цель и клиент (ADR-0017 §6) ────────────────────────────────────
# AsyncGroq — тонкий OpenAI-совместимый клиент; принимает base_url, поэтому один
# и тот же класс ходит и в Groq, и в OpenRouter, и в OpenAI. Что различается
# между провайдерами (URL, ключ, модель, reasoning_effort, лимит фото) собрано в
# LLMTarget. Прод берёт цель из settings (дефолт = Groq → поведение не меняется);
# раннер eval подставляет свою цель на провайдера, не покидая этот прод-путь.

@dataclass
class LLMTarget:
    """Куда и чем ходит LLM-клиент (ADR-0017 §6)."""

    base_url: str
    api_key: str
    model: str
    # "" = не передавать reasoning_effort (параметр специфичен для двухрежимных
    # моделей вроде qwen3.6; другие провайдеры на нём могут отдать 400).
    reasoning_effort: str = ""
    max_images: int = 5
    # Имя параметра лимита вывода. Groq/OpenRouter — "max_tokens"; OpenAI GPT-5.x
    # его отвергают и требуют "max_completion_tokens" (проверено 2026-07-27).
    max_tokens_param: str = "max_tokens"
    # Бюджет вывода. None → дефолт вызова (1024 текст / 1500 extract). Для reasoning
    # нужен запас: reasoning-токены счётаются в этот лимит и без запаса выедают JSON.
    max_output_tokens: int | None = None
    # Температура. None → не передавать (взять дефолт модели). Нужно для reasoning-
    # моделей OpenAI: они принимают ТОЛЬКО temperature=1 и дают 400 на 0.2.
    temperature: float | None = 0.2
    # Слать ли response_format={"type":"json_object"}. Anthropic-compat его отвергает
    # (хочет json_schema) → False; _extract_json и так снимает ```-обёртку ответа.
    send_response_format: bool = True
    # OpenRouter опц. читает HTTP-Referer/X-Title для рейтинга; не обязательно.
    extra_headers: dict = field(default_factory=dict)


def default_target() -> LLMTarget:
    """Прод-цель из settings (Groq по умолчанию)."""
    if not settings.groq_api_key:
        raise LLMNotConfiguredError(
            "Расчёт КБЖУ недоступен: не задан GROQ_API_KEY в .env"
        )
    return LLMTarget(
        base_url=settings.llm_base_url,
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        reasoning_effort=settings.groq_reasoning_effort,
        max_images=settings.groq_max_images,
    )


def make_client(target: LLMTarget):
    """Строит клиент под цель. Groq → AsyncGroq (его дефолтный base_url; SDK сам
    дописывает /openai/v1). Прочие провайдеры → AsyncOpenAI, который уважает
    base_url с версией пути (OpenRouter `/api/v1`, OpenAI `/v1`). Отдельный клиент
    на каждую цель — раннер держит несколько провайдеров в одном процессе."""
    if "api.groq.com" in target.base_url:
        return AsyncGroq(
            api_key=target.api_key,
            default_headers=target.extra_headers or None,
        )
    if _AsyncOpenAI is None:
        raise LLMNotConfiguredError(
            "Для не-Groq провайдера нужен пакет openai (pip install openai)"
        )
    return _AsyncOpenAI(
        api_key=target.api_key,
        base_url=target.base_url,
        default_headers=target.extra_headers or None,
    )


# Ленивый синглтон прод-клиента: импорт модуля не трогает сеть/ключ — клиент
# рождается только при первом вызове.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = make_client(default_target())
    return _client


def _cap_images(images: list, cap: int | None = None) -> list:
    """Режет список снимков до лимита модели (`cap`, по умолчанию из settings).

    Обрезка — деградация, а не ошибка: у пользователя уже есть 4-е фото в UI, и
    падать на этом хуже, чем посчитать по первым N. Но факт потери логируем —
    без этого «модель не увидела этикетку» выглядит как галлюцинация.
    Решение «ошибка vs обрезка vs два вызова» — открытый вопрос ADR-0014.
    """
    if cap is None:
        cap = settings.groq_max_images
    if len(images) > cap:
        logger.warning(
            f"Изображений {len(images)} > лимита модели {cap} "
            f"({settings.groq_model}) — в запрос уйдут первые {cap}"
        )
    return images[:cap]


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
    """OpenAI-совместимая реализация контракта `NutritionLLM` (vision + text, JSON).

    `target=None` → прод-цель из settings (Groq). Раннер eval передаёт свою цель на
    провайдера, оставаясь на этом же пути (ADR-0017 §6).
    """

    def __init__(self, target: LLMTarget | None = None, model: str | None = None):
        self._target = target
        self._client = make_client(target) if target is not None else None
        self.model = model or (target.model if target else settings.groq_model)

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

    async def _complete(
        self, user_content, system_prompt: str | None = None
    ) -> NutritionResult:
        """Общий вызов чата для текста и фото. user_content — str или список частей."""
        # reasoning_effort передаём только если задан: у двухрежимных моделей
        # (qwen3.6) без него включается thinking, и reasoning-токены выедают
        # бюджет вывода до того, как модель дойдёт до JSON → пустой content.
        # temperature=0.2 — под детерминизм JSON; None → не слать (reasoning-модели
        # OpenAI принимают только дефолт 1). response_format — не для Anthropic.
        extra: dict = {self._max_tokens_param(): self._output_budget(1024)}
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
                    {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                **extra,
            )
        except _RATE_LIMIT_ERRORS as e:
            raise LLMRateLimitError(
                "Лимит запросов к модели исчерпан, попробуй позже"
            ) from e
        except _API_ERRORS as e:
            logger.error(f"LLM API error: {e}")
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
        self, images: list[bytes], caption: str = "", mime_type: str = "image/jpeg"
    ) -> NutritionResult:
        """Одно или несколько фото блюда/упаковки (+ опц. описание) -> позиции с КБЖУ.

        Все снимки уходят ОДНИМ вызовом; кап — `settings.groq_max_images`
        (свойство модели, не домена). Промпт — прежний _SYSTEM_PROMPT: несколько
        продуктов на фото → несколько позиций.
        """
        images = _cap_images(images, self._max_images())
        parts: list[dict] = [
            {
                "type": "text",
                "text": caption.strip() or "Определи продукты на фото и оцени их КБЖУ.",
            }
        ]
        for image_bytes in images:
            b64 = base64.b64encode(image_bytes).decode()
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                }
            )
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
        """Фото блюда (кап `groq_max_images`) + название + вес → КБЖУ порции."""
        parts: list[dict] = [
            {
                "type": "text",
                "text": (
                    f'Блюдо: "{name}", вес порции: {weight_g} г. '
                    "Рассчитай суммарное КБЖУ для всей этой порции."
                ),
            }
        ]
        for b64 in _cap_images(images_b64, self._max_images()):
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
        for b64 in _cap_images(images_b64, self._max_images()):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        return await self._complete(parts, system_prompt=_COMBINED_PROMPT)
