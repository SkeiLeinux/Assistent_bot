"""services/llm_service.py — транспорт к LLM + доменные DTO расчёта КБЖУ (I6/I7).

**Что здесь есть и чего здесь больше нет.** До волны 1 файл держал и транспорт, и
три собственных промпта, и класс `LLMService` с пятью путями, каждый из которых
заставлял модель считать за порцию. Промпты и пути уехали в `resolve` (единый
системный промпт + режимные преамбулы, ТЗ §4), арифметика — в `FoodService`
(L2, §5.4). Здесь осталось ровно то, что не знает ни про КБЖУ, ни про промпты:

  - `NutritionItem` / `NutritionResult` / `UnresolvedItem` — доменные DTO ответа;
  - семейство `LLMError` — понятные пользователю ошибки (I6, REST 502);
  - `LLMTarget` / `make_client` / `default_target` — КУДА и ЧЕМ ходим;
  - `LLMClient` — один сырой вызов чата: собрать запрос, поймать `finish_reason`,
    вернуть текст и usage. Разбор ответа — не его дело (это C1 в `resolve/checks`).

**Направление зависимости не изменилось:** `resolve` → `services`, не наоборот.
Транспорт и DTO — низ, извлечение и лестница — верх. Поэтому контракт
import-linter «resolve — leaf внутри Nutrition» остался нетронутым: код подгонять
под контракт не пришлось, потому что слои и так лежат в эту сторону.

**I7 (провайдер-агностичность):** FoodService зависит от инжектируемого
извлекателя, а не от вендора; вся вендорская специфика собрана в `LLMTarget`,
поэтому смена цели — правка конфига, а не этого файла. Раннер eval
(`tests/eval/run_eval.py`) — первый потребитель этой абстракции: он подставляет
свою цель, оставаясь на прод-пути. Живых вызовов провайдера из pytest нет (GAP-6).

Провайдер (ADR-0017 §16): OpenAI `gpt-5.4-mini`, клиент `AsyncOpenAI`.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

from openai import APIError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# ── Контракт данных ─────────────────────────────────────────────────────────

class NutritionItem(BaseModel):
    """Одна позиция: продукт/блюдо с КБЖУ уже для съеденной порции (абсолют, I1).

    Провенанс (ADR-0013, §5): `source`/`source_ref` происходят от резолвера и
    сохраняются в FoodEntry. `matched_name` и `warnings` — только транспорт
    analyze→confirm (в БД не пишутся). `source=None` на «голой» позиции (ручной
    ввод) → save_entries подставит дефолт вызова.
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
    # Коды флагов проверок §6 и маршрута §7 (arith_mismatch, kj_suspected,
    # weight_mismatch, label_disagrees_with_source, ...). Человекочитаемый текст
    # рисует фронт по коду — так один и тот же флаг не расходится по формулировкам.
    warnings: list[str] = Field(default_factory=list)
    # per100g — транспорт для confirm-экрана (пересчёт КБЖУ при правке веса, ADR-0013
    # опция C). НЕ персистится (FoodEntry хранит абсолют за порцию, I1).
    kcal_100g: float | None = None
    protein_100g: float | None = None
    fat_100g: float | None = None
    carbs_100g: float | None = None


class UnresolvedItem(BaseModel):
    """Позиция без чисел (ТЗ §6.6): ни модель, ни источники не смогли ответить.

    Отдельный массив, а не nullable-КБЖУ внутри `items`: сделать `kcal`
    опциональным значит заставить каждого потребителя (фронт, save_entries,
    аналитику, дайджест) проверять на null в пути, где числа есть почти всегда.
    Сохранение такой позиции запрещено — запись с нулями выглядит как данные и
    портит дневные итоги, прогресс к целям и дайджест.
    """

    name: str | None = None
    note: str | None = None
    reason: Literal["unreadable", "no_source"] = "unreadable"


class NutritionResult(BaseModel):
    """Обёртка-объект (JSON-режим требует объект на верхнем уровне, не массив)."""

    items: list[NutritionItem]
    unresolved: list[UnresolvedItem] = Field(default_factory=list)


# ── Ошибки ──────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Базовая ошибка LLM с понятным пользователю сообщением (REST: 502)."""


class LLMNotConfiguredError(LLMError):
    """Не задан ключ API — модуль расчёта КБЖУ недоступен."""


class LLMRateLimitError(LLMError):
    """Превышен лимит запросов к модели (429) — стоит повторить позже."""


# ── Провайдер-цель и клиент (ADR-0017 §6) ────────────────────────────────────
# AsyncOpenAI уважает base_url С версией пути, поэтому один и тот же класс ходит
# и в OpenAI (`/v1`), и в OpenRouter (`/api/v1`), и в Anthropic-compat. Что
# различается между провайдерами (URL, ключ, модель, reasoning_effort, лимит фото,
# имя параметра лимита вывода) собрано в LLMTarget. Прод берёт цель из settings;
# раннер eval подставляет свою цель на провайдера, не покидая этот прод-путь.

@dataclass
class LLMTarget:
    """Куда и чем ходит LLM-клиент (ADR-0017 §6)."""

    base_url: str
    api_key: str
    model: str
    # "" = не передавать reasoning_effort (параметр специфичен для двухрежимных
    # моделей; другие провайдеры на нём могут отдать 400).
    reasoning_effort: str = ""
    max_images: int = 5
    # Имя параметра лимита вывода. OpenAI GPT-5.x требуют "max_completion_tokens"
    # и отвергают "max_tokens" (проверено 2026-07-27); OpenRouter/Anthropic-compat
    # — наоборот. Дефолт = под прод-провайдера.
    max_tokens_param: str = "max_completion_tokens"
    # Бюджет вывода. None → считается от числа снимков (§5.3). Для reasoning нужен
    # запас: reasoning-токены счётаются в этот лимит и без запаса выедают JSON.
    max_output_tokens: int | None = None
    # Температура. None → не передавать (взять дефолт модели). Нужно для reasoning-
    # моделей OpenAI: они принимают ТОЛЬКО temperature=1 и дают 400 на 0.2.
    temperature: float | None = 0.2
    # Слать ли response_format={"type":"json_object"}. Anthropic-compat его отвергает
    # (хочет json_schema) → False; снятие ```-обёртки в checks.extract_json это чинит.
    send_response_format: bool = True
    # OpenRouter опц. читает HTTP-Referer/X-Title для рейтинга; не обязательно.
    extra_headers: dict = field(default_factory=dict)


def default_target() -> LLMTarget:
    """Прод-цель из settings (ADR-0017 §16: OpenAI `gpt-5.4-mini`)."""
    if not settings.llm_api_key:
        raise LLMNotConfiguredError(
            "Расчёт КБЖУ недоступен: не задан LLM_API_KEY в .env"
        )
    return LLMTarget(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_vision_model,
        reasoning_effort=settings.llm_reasoning_effort,
        max_images=settings.llm_max_images,
        max_tokens_param=settings.llm_max_tokens_param,
        temperature=settings.llm_temperature,
    )


def make_client(target: LLMTarget) -> AsyncOpenAI:
    """Строит клиент под цель — всегда `AsyncOpenAI`.

    Он уважает base_url С версией пути (OpenAI `/v1`, OpenRouter `/api/v1`), чего
    прежний вендорский SDK не умел: тот хардкодил путь и игнорировал версию в URL
    (ADR-0017 §14.1) — именно поэтому здесь была развилка по вендору. Отдельный
    клиент на каждую цель — раннер держит несколько провайдеров в одном процессе.
    """
    return AsyncOpenAI(
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


# ── Сырой вызов чата ─────────────────────────────────────────────────────────

@dataclass
class LLMCall:
    """Результат одного вызова: текст ответа + всё, по чему судят о его качестве."""

    text: str
    finish_reason: str | None = None
    usage: dict | None = None
    retries: int = 0


class LLMClient:
    """Один вызов чата. Не знает ни про КБЖУ, ни про промпты, ни про JSON.

    Раньше этот код существовал в ДВУХ копиях — в `LLMService._complete` и в
    `LLMExtractor._complete` — и копии успели разойтись по бюджету вывода
    (1024 против 1500). Свод в одно место закрывает D7 из ТЗ §1.
    """

    def __init__(self, target: LLMTarget | None = None, model: str | None = None):
        self._target = target
        self._client = make_client(target) if target is not None else None
        self.model = model or (target.model if target else settings.llm_vision_model)

    # ── параметры цели (target перекрывает settings) ────────────────────────

    def _client_for(self):
        return self._client if self._client is not None else _get_client()

    def _reasoning_effort(self) -> str:
        if self._target is not None:
            return self._target.reasoning_effort
        return settings.llm_reasoning_effort

    @property
    def max_images(self) -> int:
        if self._target is not None:
            return self._target.max_images
        return settings.llm_max_images

    def _max_tokens_param(self) -> str:
        if self._target is not None:
            return self._target.max_tokens_param
        return settings.llm_max_tokens_param

    def _temperature(self) -> float | None:
        if self._target is not None:
            return self._target.temperature
        return settings.llm_temperature

    def _send_response_format(self) -> bool:
        return self._target.send_response_format if self._target is not None else True

    def output_budget(self, n_images: int = 0) -> int:
        """Бюджет вывода под размер ввода (ТЗ §5.3).

        `base + per_item × N`, но не ниже `llm_output_tokens_min`. Основание —
        фактический расход на ярусе A: 1802–2174 токена на вызов, прогноз ~1950
        сошёлся с точностью <8%. Цель у батча своя, потому что число позиций в
        ответе линейно по числу снимков в ЭТОМ вызове, а не во всём вводе.
        """
        if self._target is not None and self._target.max_output_tokens:
            return self._target.max_output_tokens
        computed = (
            settings.llm_output_tokens_base
            + settings.llm_output_tokens_per_item * max(0, n_images)
        )
        return max(computed, settings.llm_output_tokens_min)

    # ── сам вызов ───────────────────────────────────────────────────────────

    async def complete(
        self,
        *,
        system: str,
        user_content,
        n_images: int = 0,
        budget: int | None = None,
        _attempt: int = 0,
    ) -> LLMCall:
        """Вызов чата. `finish_reason="length"` → один ретрай с ×2 бюджетом (§5.3).

        Обрезка по лимиту вывода без этой проверки неотличима от «модель вернула
        не JSON»: пользователь видит «не удалось разобрать ответ», а причина —
        что ответ не поместился. Второй раз подряд — честная ошибка с внятным
        текстом, а не третья попытка.
        """
        budget = budget if budget is not None else self.output_budget(n_images)
        # reasoning_effort передаём только если задан: у двухрежимных моделей без
        # него включается thinking, и reasoning-токены выедают бюджет вывода до
        # того, как модель дойдёт до JSON → пустой content. temperature=0.2 — под
        # детерминизм JSON; None → не слать. response_format — не для Anthropic.
        extra: dict = {self._max_tokens_param(): budget}
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
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                **extra,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(
                "Лимит запросов к модели исчерпан, попробуй позже"
            ) from e
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            raise LLMError("Сервис расчёта КБЖУ временно недоступен") from e

        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        usage = getattr(response, "usage", None)
        call = LLMCall(
            text=choice.message.content or "",
            finish_reason=finish_reason,
            usage=usage.model_dump() if usage is not None else None,
            retries=_attempt,
        )
        if finish_reason == "length":
            if _attempt == 0:
                logger.warning(
                    "LLM ответ обрезан по лимиту вывода (%s токенов) — ретрай с ×2",
                    budget,
                )
                return await self.complete(
                    system=system,
                    user_content=user_content,
                    n_images=n_images,
                    budget=budget * 2,
                    _attempt=1,
                )
            raise LLMError(
                "Ответ модели не помещается в лимит — пришли меньше снимков за раз"
            )
        return call
