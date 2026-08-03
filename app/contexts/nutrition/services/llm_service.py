"""services/llm_service.py — транспорт к LLM + доменные DTO расчёта КБЖУ (I6/I7).

**Что здесь есть и чего здесь больше нет.** До волны 1 файл держал и транспорт, и
три собственных промпта, и класс `LLMService` с пятью путями, каждый из которых
заставлял модель считать за порцию. Промпты и пути уехали в `resolve` (единый
системный промпт + режимные преамбулы, ТЗ §4), арифметика — в `FoodService`
(L2, §5.4). Здесь осталось ровно то, что не знает ни про КБЖУ, ни про промпты:

  - `NutritionItem` / `NutritionResult` / `UnresolvedItem` — доменные DTO ответа;
  - семейство `LLMError` — понятные пользователю ошибки (I6, REST 502);
  - `LLMTarget` / `make_client` / `default_targets` — КУДА и ЧЕМ ходим;
  - `LLMClient` — один сырой вызов чата: собрать запрос, поймать `finish_reason`,
    вернуть текст и usage. Разбор ответа — не его дело (это C1 в `resolve/checks`);
  - `classify_failure` + `check_model_available` — отказоустойчивость (ADR-0018).

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

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable, Literal

from openai import APIError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, Field

from app.config import settings
from app.contexts.nutrition.services import observability as obs

logger = logging.getLogger(__name__)


# ── Контракт данных ─────────────────────────────────────────────────────────

class AlternativeNutrition(BaseModel):
    """Вторая версия чисел той же позиции (ТЗ §7.2, ADR-0013 ревизия).

    Появляется, когда этикетка разошлась с внешним источником больше чем на
    `resolver_disagree_pct`. Числа НЕ подменяются (принцип §2.4) — обе версии
    показываются пользователю, и выбирает он.

    Транспорт analyze→confirm, в БД не пишется: пользователь выбирает ОДНУ
    версию, и в дневник уезжает она, а не пара. Отдельная модель, а не
    вложенный `NutritionItem`, потому что альтернатива не самостоятельная
    позиция: у неё нет ни имени в дневнике, ни флага «сохранить в каталог», ни
    собственной альтернативы — вложенность породила бы все три вопроса.
    """

    kcal: float
    protein: float
    fat: float
    carbs: float
    source: str | None = None
    source_ref: str | None = None
    matched_name: str | None = None
    # per100g — чтобы правка веса пересчитывала и альтернативу тоже (опция C).
    kcal_100g: float | None = None
    protein_100g: float | None = None
    fat_100g: float | None = None
    carbs_100g: float | None = None


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
    # Вторая версия чисел при расхождении этикетки с источником >15% (§7.2).
    # До блока 15 резолвер её вычислял, а DTO терял — то есть механизм сверки
    # label-preferred, ради которого ревизовался ADR-0013, до пользователя не
    # доходил вовсе. Не персистится: в дневник уезжает выбранная версия.
    alternative: AlternativeNutrition | None = None


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
    # Роль в списке целей (ADR-0018 §2.1). Не «какой вендор», а «первая попытка
    # или резерв»: позиции, посчитанные резервом, помечаются флагом
    # `fallback_provider`, а состояние «работаем на резерве» обязано быть видно.
    role: Literal["primary", "fallback"] = "primary"
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
    # Таймаут одного вызова, секунды (ADR-0018 §2.3). Отдаётся в SDK параметром
    # `timeout=`: он корректно рвёт соединение, тогда как обёртка
    # `asyncio.wait_for` бросила бы задачу и оставила висящий сокет.
    #
    # Значение — СВОЙСТВО ЦЕЛИ, а не константа прода: 20 с выбраны под медиану
    # победителя 1.3 с (запас ×15), но раннер eval меряет и заведомо медленные
    # модели (у `qwen/qwen3.6-27b` замерено 21.9 с на вызов), и для них прод-
    # таймаут превратил бы измерение в отказ.
    timeout_s: float = 20.0
    # Сколько раз повторять на 429. `None` = взять `llm_retry_429` из конфига.
    # Явный 0 нужен раннеру: у него СВОЙ backoff (10·2^n) и своя метрика
    # латентности, а ретраи внутри клиента попали бы в замеряемое время.
    retry_429: int | None = None
    # OpenRouter опц. читает HTTP-Referer/X-Title для рейтинга; не обязательно.
    extra_headers: dict = field(default_factory=dict)


def _api_key() -> str:
    """Прод-ключ в открытом виде. SecretStr — чтобы не утекал в трейсбеки."""
    key = settings.llm_api_key
    raw = key.get_secret_value() if key is not None else ""
    if not raw:
        raise LLMNotConfiguredError(
            "Расчёт КБЖУ недоступен: не задан LLM_API_KEY в .env"
        )
    return raw


def default_target() -> LLMTarget:
    """Первичная прод-цель из settings (ADR-0017 §16: OpenAI `gpt-5.4-mini`)."""
    return LLMTarget(
        base_url=settings.llm_base_url,
        api_key=_api_key(),
        model=settings.llm_vision_model,
        reasoning_effort=settings.llm_reasoning_effort,
        max_images=settings.llm_max_images,
        max_tokens_param=settings.llm_max_tokens_param,
        temperature=settings.llm_temperature,
        timeout_s=settings.llm_timeout_s,
        role="primary",
    )


def fallback_target() -> LLMTarget:
    """Вторичная цель (ADR-0018, вариант B): `gpt-5.4`, всё остальное — то же.

    Ни отдельного ключа, ни отдельного base_url, ни отдельного набора параметров:
    вариант B выбран именно потому, что второго кодового пути не возникает
    (драйвер D4). Отличается ровно имя модели.
    """
    return LLMTarget(
        **{
            **vars(default_target()),
            "model": settings.llm_fallback_vision_model,
            "role": "fallback",
        }
    )


# Первичная цель, снятая с первого места guard'ом присутствия модели (§8.4).
# Не булев флаг, а имя модели: guard проверяет конкретную модель, и если её
# вернут обратно (или сменят `llm_vision_model` в конфиге), демоция не должна
# пережить свою причину.
_demoted_model: str | None = None


def demote_primary(model: str) -> None:
    """Убрать первичную модель с первого места до перезапуска (ADR-0018 §2.4).

    Модель уходит в конец списка, а не удаляется: guard мог не достучаться до
    `/models` по сети, и выбрасывать рабочую цель по недоказанному подозрению
    хуже, чем попробовать её последней.
    """
    global _demoted_model
    if _demoted_model != model:
        logger.warning("LLM: модель %s снята с первого места (guard)", model)
    _demoted_model = model


def restore_primary() -> None:
    """Guard увидел модель на месте — демоция снимается."""
    global _demoted_model
    if _demoted_model is not None:
        logger.info("LLM: модель %s снова доступна, порядок целей восстановлен",
                    _demoted_model)
    _demoted_model = None


def default_targets() -> list[LLMTarget]:
    """Прод-список целей по приоритету (ADR-0018 §2.1): первичная, затем резерв."""
    primary, fallback = default_target(), fallback_target()
    if _demoted_model == primary.model and fallback.model != primary.model:
        return [fallback, primary]
    return [primary, fallback]


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


# Ленивый кэш клиентов по (base_url, ключ): импорт модуля не трогает ни сеть, ни
# ключ. Ключ — не модель: у первичной и вторичной цели вендор и ключ одни и те же
# (вариант B), и второй HTTP-пул им ни к чему.
_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def _client_for(target: LLMTarget) -> AsyncOpenAI:
    key = (target.base_url, target.api_key)
    client = _clients.get(key)
    if client is None:
        client = _clients[key] = make_client(target)
    return client


# ── Алерты (ADR-0018 §2.2/§2.4) ──────────────────────────────────────────────
# Контекст не строит Bot сам: канал алерта инъектится композиционным корнем
# (main.py → Notifier.alert_admin), как это уже сделано для change-событий Lists.
# Без хука алерт деградирует в лог — модуль остаётся импортируемым в тестах и в
# раннере eval, где никакого Telegram нет.

_alert_hook: Callable[[str], Awaitable[None]] | None = None


def set_llm_alert_hook(hook: Callable[[str], Awaitable[None]] | None) -> None:
    global _alert_hook
    _alert_hook = hook


async def _alert(text: str) -> None:
    logger.error("LLM alert: %s", text)
    if _alert_hook is None:
        return
    try:
        await _alert_hook(text)
    except Exception as exc:  # noqa: BLE001 — алерт не должен валить разбор
        logger.error("LLM: не удалось отправить алерт: %s", exc)


# Дата последнего алерта о переключении. ADR-0018 §5 просит уведомлять о ПЕРВОМ
# за сутки переключении: без дедупликации всплеск 5xx превратил бы алерт в спам,
# после которого его перестают читать — то есть вернул бы немой отказ другим путём.
_last_switch_alert: date | None = None


async def _alert_switch(text: str) -> bool:
    """Алерт о переключении. Возвращает, был ли он ОТПРАВЛЕН (а не подавлен).

    Возвращает, а не молчит, ради строки лога §9: поле `alerted` обязано
    сообщать факт, а не повторять правило дедупликации во втором месте. Повтор
    правила разъехался бы с ним при первой же правке, и лог утверждал бы об
    отправленном алерте, которого не было.
    """
    global _last_switch_alert
    today = date.today()
    if _last_switch_alert == today:
        logger.warning("LLM: %s (алерт уже отправлен сегодня)", text)
        return False
    _last_switch_alert = today
    await _alert(text)
    return True


# ── Классификация отказа (ADR-0018 §2.2, ТЗ §8.3) ────────────────────────────

FailureClass = Literal["model", "transient", "account", "vendor", "user"]

# Классы, при которых имеет смысл менять цель. Матрица СУЖЕНА намеренно, и это
# не недоработка: ключ и аккаунт у первичной и вторичной цели ОДНИ И ТЕ ЖЕ
# (вариант B ADR-0018). Значит 401, 403, `insufficient_quota` и org-level 429
# резервом не лечатся в принципе — переключение лишь добавит `llm_timeout_s`
# ожидания перед тем же самым отказом, а пользователь будет ждать вдвое дольше
# ради предрешённого результата. Переключаемся тогда и только тогда, когда отказ
# специфичен для МОДЕЛИ.
SWITCHABLE: frozenset[str] = frozenset({"model", "transient"})

# Коды ошибок, однозначно указывающие на аккаунт, а не на модель. `insufficient_quota`
# приходит с 429 — тем же статусом, что и обычный rate limit, поэтому статуса мало.
_ACCOUNT_CODES = frozenset({
    "invalid_api_key", "insufficient_quota", "account_deactivated",
    "billing_hard_limit_reached",
})
_MODEL_CODES = frozenset({
    "model_not_found", "deprecated_model", "model_deprecated", "unknown_model",
})


def _error_code(exc: BaseException) -> str:
    """Код ошибки провайдера в нижнем регистре ('' — нет кода).

    SDK кладёт его в `.code`, но не всегда: у части ответов он лежит только в
    теле. Читаем оба места, потому что от этого кода зависит развилка
    «переключаться или нет».
    """
    code = getattr(exc, "code", None)
    if not code:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            code = (err or {}).get("code") if isinstance(err, dict) else body.get("code")
    return str(code).lower() if code else ""


def classify_failure(exc: BaseException) -> FailureClass:
    """Отказ → класс из матрицы ADR-0018 §2.2.

    Отдельная функция, а не цепочка `except` по месту вызова: это правило
    решения, от которого зависит поведение продукта при следующем снятии модели,
    и его надо уметь протестировать без сети и без провайдера.
    """
    status = getattr(exc, "status_code", None)
    code = _error_code(exc)
    message = str(getattr(exc, "message", "") or exc).lower()

    if code in _ACCOUNT_CODES:
        return "account"
    if code in _MODEL_CODES:
        return "model"
    if status in (401, 403):
        return "account"
    if status == 404:
        # 404 у чат-эндпоинта означает «нет такой модели»: сам путь /chat/completions
        # существует всегда, иначе не работал бы ни один вызов.
        return "model"
    if status == 429:
        # Дошло сюда — ретраи §8.2 уже исчерпаны. Обычный rate limit модели
        # лечится сменой модели, квота аккаунта — нет (отсеяна кодом выше).
        return "transient"
    if status is not None and 500 <= status < 600:
        return "transient"
    if status == 400:
        # Кривой запрос (слишком большие изображения, неподдерживаемый параметр).
        # Исключение — когда провайдер сообщает про модель именно четырёхсотым.
        return "model" if "model" in message else "user"
    if status is not None and 400 <= status < 500:
        return "user"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "transient"
    if isinstance(exc, APIError):
        # APIConnectionError/APITimeoutError статуса не несут.
        return "transient"
    return "vendor"


# ── Сырой вызов чата ─────────────────────────────────────────────────────────

class _TargetFailure(Exception):
    """Цель исчерпала свои попытки. Несёт и сырой отказ, и готовую ошибку.

    Сырой нужен классификатору (`classify_failure` смотрит на статус и код),
    готовый — пользователю. Раньше эти две роли совмещал один `LLMError`, и
    решение «переключаться или нет» пришлось бы принимать по тексту сообщения.
    """

    def __init__(self, original: BaseException, error: LLMError) -> None:
        super().__init__(str(original))
        self.original = original
        self.error = error


def _retry_delay(exc: BaseException, attempt: int) -> float:
    """Пауза перед повтором 429: 1 с → 4 с + jitter, но `Retry-After` главнее.

    Jitter — не украшение: два параллельных батча (§5.2) стартуют одновременно и
    без него повторяли бы запрос одновременно же, воспроизводя тот самый лимит.
    """
    after = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        try:
            after = float(headers.get("retry-after"))
        except (TypeError, ValueError):
            after = None
    if after is not None and after >= 0:
        return after
    return min(1.0 * (4 ** attempt), 8.0) + random.uniform(0.0, 0.5)


@dataclass
class LLMCall:
    """Результат одного вызова: текст ответа + всё, по чему судят о его качестве."""

    text: str
    finish_reason: str | None = None
    usage: dict | None = None
    retries: int = 0
    # Какая цель в итоге ответила. Читает извлекатель: позиции, посчитанные
    # резервом, получают флаг `fallback_provider` (ADR-0018 §2.5).
    target_role: str = "primary"
    model: str | None = None


class LLMClient:
    """Вызов чата с ретраями и резервной целью. Не знает ни про КБЖУ, ни про JSON.

    Раньше этот код существовал в ДВУХ копиях — в `LLMService._complete` и в
    `LLMExtractor._complete` — и копии успели разойтись по бюджету вывода
    (1024 против 1500). Свод в одно место закрывает D7 из ТЗ §1.

    **Порядок попыток (ADR-0018 §2.3): ретраи первичны по отношению к
    переключению.** Сначала исчерпываем попытки на текущей модели, и только
    потом меняем цель — иначе транзиентный 429 уводил бы разбор на резерв,
    который упрётся в тот же лимит аккаунта.

    Явный `target` (раннер eval) отключает резерв: раннер измеряет КОНКРЕТНУЮ
    модель, и молчаливый уход на другую испортил бы замер, ради которого он
    запущен.
    """

    def __init__(
        self,
        target: LLMTarget | None = None,
        model: str | None = None,
        targets: list[LLMTarget] | None = None,
    ):
        if targets is not None:
            explicit = list(targets)
        elif target is not None:
            explicit = [target]
        else:
            explicit = None
        if explicit and model:
            explicit[0] = LLMTarget(**{**vars(explicit[0]), "model": model})
        self._explicit = explicit
        self._target = explicit[0] if explicit else None
        self.model = model or (self._target.model if self._target
                               else settings.llm_vision_model)
        # Наблюдаемость: какая цель ответила на последний вызов.
        self.last_target_role = "primary"
        self.last_model = self.model

    # ── параметры цели ──────────────────────────────────────────────────────
    # Остальные параметры вызова (reasoning_effort, temperature, имя параметра
    # бюджета, response_format) читаются ИЗ ЦЕЛИ в `_create`, а не из settings:
    # у списка целей каждая отвечает за себя, и «settings-по-умолчанию» здесь
    # означало бы, что резерв тихо поедет с параметрами первичной цели.

    def _targets(self) -> list[LLMTarget]:
        return self._explicit if self._explicit is not None else default_targets()

    @property
    def max_images(self) -> int:
        # Лимит фото — свойство провайдера, и у обеих целей варианта B он один.
        if self._target is not None:
            return self._target.max_images
        return settings.llm_max_images

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
    ) -> LLMCall:
        """Вызов чата по списку целей (ADR-0018): ретраи, затем смена цели.

        Возвращается первый успешный ответ. Отказ, специфичный для модели,
        уводит на следующую цель; отказ аккаунтного/пользовательского уровня
        доходит до пользователя честной ошибкой — резерв его не лечит (§8.3).
        """
        targets = self._targets()
        last: LLMError | None = None
        for index, target in enumerate(targets):
            try:
                call = await self._complete_on(
                    target, system=system, user_content=user_content,
                    n_images=n_images, budget=budget,
                )
            except _TargetFailure as failure:
                last = failure.error
                kind = classify_failure(failure.original)
                is_last = index + 1 >= len(targets)
                if kind in SWITCHABLE and not is_last:
                    nxt = targets[index + 1]
                    # Структурная строка §1.3 рядом с алертом, а не вместо него:
                    # алерт дедуплицируется («первое за сутки», §5 ADR-0018),
                    # поэтому по одним алертам нельзя отличить единичный сбой от
                    # постоянной работы на резерве — а это ровно та разница,
                    # ради которой блок 13 существует. `alerted` берётся у самого
                    # алерта, а не вычисляется здесь заново: второе место с тем
                    # же правилом рано или поздно разъедется с первым.
                    alerted = await _alert_switch(
                        f"LLM: модель {target.model} не отвечает (класс отказа: "
                        f"{kind}). Переключение на {nxt.model}. "
                        f"Причина: {failure.original}"
                    )
                    logger.warning("%s", obs.switch_line(
                        from_model=target.model, to_model=nxt.model,
                        failure=kind, alerted=alerted,
                    ))
                    continue
                if kind in ("account", "vendor"):
                    # Ошибку пользователю всё равно отдадим, но админ обязан
                    # узнать: сам по себе текст «сервис недоступен» — это ровно
                    # тот немой отказ, из-за которого 17.07.26 нашли через неделю.
                    await _alert(
                        f"LLM: отказ уровня «{kind}» на модели {target.model}, "
                        f"резервом не лечится: {failure.original}"
                    )
                raise failure.error
            self.last_target_role = target.role
            self.last_model = target.model
            return call
        raise last or LLMError("Сервис расчёта КБЖУ временно недоступен")

    async def _complete_on(
        self,
        target: LLMTarget,
        *,
        system: str,
        user_content,
        n_images: int,
        budget: int | None,
    ) -> LLMCall:
        """Все попытки на ОДНОЙ цели (§8.2). Исчерпав их, бросает `_TargetFailure`."""
        budget = budget if budget is not None else self.output_budget(n_images)
        model = target.model if self._explicit is None else self.model
        retry_429 = (
            settings.llm_retry_429 if target.retry_429 is None else target.retry_429
        )
        attempts = 0
        length_retried = False
        transient_retried = False
        rate_limit_attempts = 0

        while True:
            try:
                response = await self._create(target, model, system, user_content, budget)
            except RateLimitError as exc:
                # 429 — единственный класс с задержкой между попытками: повтор
                # «сразу же» упирается в то же окно лимита. До llm_retry_429
                # попыток, backoff 1 с → 4 с + jitter, `Retry-After` уважаем.
                if (classify_failure(exc) == "transient"
                        and rate_limit_attempts < retry_429):
                    delay = _retry_delay(exc, rate_limit_attempts)
                    rate_limit_attempts += 1
                    attempts += 1
                    logger.warning(
                        "LLM 429 на %s: попытка %d/%d через %.1f с",
                        model, rate_limit_attempts, retry_429, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise _TargetFailure(
                    exc,
                    LLMRateLimitError("Лимит запросов к модели исчерпан, попробуй позже"),
                ) from exc
            except APIError as exc:
                # 5xx / timeout / обрыв — один ретрай БЕЗ задержки: сеть уже
                # съела время, а причина чаще всего мгновенная.
                if not transient_retried and classify_failure(exc) == "transient":
                    transient_retried = True
                    attempts += 1
                    logger.warning("LLM: транзиентный сбой на %s (%s) — один ретрай",
                                   model, exc)
                    continue
                logger.error("LLM API error (%s): %s", model, exc)
                raise _TargetFailure(
                    exc, LLMError("Сервис расчёта КБЖУ временно недоступен")
                ) from exc

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            usage = getattr(response, "usage", None)

            if finish_reason == "length":
                # Обрезка по лимиту вывода без этой проверки неотличима от
                # «модель вернула не JSON»: пользователь видит «не удалось
                # разобрать ответ», а причина — что ответ не поместился.
                if not length_retried:
                    length_retried = True
                    attempts += 1
                    budget *= 2
                    logger.warning(
                        "LLM ответ обрезан по лимиту вывода — ретрай с бюджетом %s",
                        budget,
                    )
                    continue
                # Не `_TargetFailure`: другая модель того же вендора вернёт ответ
                # такой же длины, переключение здесь ничего не чинит.
                raise LLMError(
                    "Ответ модели не помещается в лимит — пришли меньше снимков за раз"
                )

            return LLMCall(
                text=choice.message.content or "",
                finish_reason=finish_reason,
                usage=usage.model_dump() if usage is not None else None,
                retries=attempts,
                target_role=target.role,
                model=model,
            )

    async def _create(self, target: LLMTarget, model: str, system, user_content, budget):
        """Сам HTTP-вызов. Вынесен, чтобы вся политика попыток читалась выше."""
        # reasoning_effort передаём только если задан: у двухрежимных моделей без
        # него включается thinking, и reasoning-токены выедают бюджет вывода до
        # того, как модель дойдёт до JSON → пустой content. temperature=0.2 — под
        # детерминизм JSON; None → не слать. response_format — не для Anthropic.
        extra: dict = {target.max_tokens_param: budget}
        if target.reasoning_effort:
            extra["reasoning_effort"] = target.reasoning_effort
        if target.temperature is not None:
            extra["temperature"] = target.temperature
        if target.send_response_format:
            extra["response_format"] = {"type": "json_object"}
        return await _client_for(target).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            # Таймаут отдаём SDK (§8.1): он рвёт соединение, а asyncio.wait_for
            # бросил бы задачу и оставил висящий сокет.
            timeout=target.timeout_s,
            **extra,
        )


# ── Guard присутствия модели (ADR-0018 §2.4, ТЗ §8.4) ────────────────────────
# Ровно эта проверка отловила бы отказ 17.07.26: вендор снял модель, уведомив
# письмом за месяц, письма никто не прочитал, и расчёт КБЖУ умер немо — узнали
# через неделю от пользователя. Список моделей провайдер отдаёт бесплатно, а
# один запрос в сутки превращает «через неделю» в «в тот же день».


async def list_models(target: LLMTarget) -> set[str] | None:
    """Имена моделей у провайдера. `None` — спросить не удалось (это НЕ «нет»).

    Различие принципиально: «модель отсутствует» — повод переключиться и позвать
    админа, «сеть не ответила» — повод написать в лог. Свести их в один `bool`
    значило бы демотировать рабочую модель при каждом сетевом сбое.
    """
    try:
        page = await _client_for(target).models.list(timeout=target.timeout_s)
        return {getattr(m, "id", "") for m in page.data}
    except Exception as exc:  # noqa: BLE001 — guard не имеет права уронить старт
        logger.warning("LLM guard: не удалось получить список моделей: %s", exc)
        return None


async def check_models(*, alert: bool = True) -> bool | None:
    """Проверить присутствие первичной модели. `None` — проверка не состоялась.

    Побочный эффект намеренный: при отсутствии модели она снимается с первого
    места (`demote_primary`), то есть следующий разбор пойдёт на резерв, не
    потратив попытку на заведомо мёртвую цель.
    """
    if not settings.llm_guard_enabled:
        return None
    try:
        primary = default_target()
    except LLMNotConfiguredError:
        # Ключа нет — расчёт КБЖУ и так недоступен, а guard об этом сообщать не
        # должен: сообщение уже придёт пользователю при первом же разборе.
        return None

    models = await list_models(primary)
    # Три статуса, а не два (ADR-0018 §2.4): «спросить не удалось» — не «модели
    # нет». В логе они обязаны различаться так же, как в коде, иначе сетевой
    # сбой будет читаться как снятие модели — то есть как инцидент 17.07.26.
    if models is None:
        logger.info("%s", obs.guard_line(
            model=primary.model, status=obs.GUARD_UNKNOWN
        ))
        return None
    if primary.model in models:
        logger.info("%s", obs.guard_line(
            model=primary.model, status=obs.GUARD_PRESENT
        ))
        restore_primary()
        return True

    logger.warning("%s", obs.guard_line(
        model=primary.model, status=obs.GUARD_MISSING
    ))
    demote_primary(primary.model)
    if alert:
        await _alert(
            f"LLM: модель {primary.model} отсутствует у провайдера "
            f"({primary.base_url}). Расчёт КБЖУ переведён на резерв "
            f"{settings.llm_fallback_vision_model}. Проверь ADR-0018 §2.4."
        )
    return False


async def run_startup_guard() -> bool | None:
    """Проверка при старте. Никогда не бросает: бот с неизвестным статусом модели
    полезнее бота, который не поднялся из-за недоступного `/v1/models` (§8.4)."""
    try:
        return await check_models()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM guard: проверка при старте не выполнена: %s", exc)
        return None


def make_llm_model_guard(interval_s: float | None = None):
    """Суточная job для generic-планировщика (ADR-0003).

    Планировщик тикает раз в минуту, поэтому интервал держим сами: гонять
    `/models` каждую минуту незачем, а заводить второй планировщик ради одной
    проверки — тем более.
    """
    interval = settings.llm_guard_interval_s if interval_s is None else interval_s
    state: dict[str, float] = {}

    async def llm_model_guard() -> None:
        now = asyncio.get_running_loop().time()
        last = state.get("last")
        if last is not None and now - last < interval:
            return
        state["last"] = now
        await run_startup_guard()

    return llm_model_guard
