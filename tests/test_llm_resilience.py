"""Блок 11 — отказоустойчивость LLM (ТЗ контракта §8, ADR-0018) + SecretStr.

Все три предмета этого файла — про ОДИН класс дефекта: систему, которая ломается
так, что никто не узнаёт.

  - **17.07.2026** вендор снял модель. Расчёт КБЖУ отказал немо: `APIError`
    маскировался под «сервис временно недоступен», уведомление пришло почтой за
    месяц, узнали через неделю от пользователя. → §8.3 (переключение на резерв) и
    §8.4 (guard по `/models`, суточный).
  - **02.08.2026** при выкатке pydantic напечатал значение ключа в трейсбек, и
    `GROQ_API_KEY` уехал в `journalctl` открытым текстом. → `hide_input_in_errors`
    + `SecretStr` (`deploy-wave1-2026-08-02.md` §2).

**Матрица §8.3 сужена намеренно, и половина тестов здесь именно про это.** Ключ и
аккаунт у первичной и вторичной цели ОДНИ И ТЕ ЖЕ (вариант B ADR-0018), поэтому
401, `insufficient_quota` и org-level 429 резервом не лечатся: переключение лишь
добавит `llm_timeout_s` ожидания перед тем же самым отказом. Тесты «переключения
НЕ происходит» — это проверка сужения, а не проверка того, что фича недоделана.

Живых вызовов провайдера нет (GAP-6): весь транспорт фейковый.
"""

import asyncio
import json

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)
from pydantic import SecretStr, ValidationError

import app.contexts.nutrition.services.llm_service as llm_mod
from app.config import Settings, settings
from app.contexts.nutrition.resolve.checks import FLAG_FALLBACK_PROVIDER
from app.contexts.nutrition.resolve.extractor import LLMExtractor


# ── Фейковый транспорт ───────────────────────────────────────────────────────

_REQUEST = httpx.Request("POST", "https://api.example/v1/chat/completions")


def _http_error(cls, status: int, code: str | None = None, message: str = "boom"):
    """Настоящее исключение SDK: классификатор читает и статус, и код тела."""
    response = httpx.Response(status, request=_REQUEST)
    body = {"error": {"message": message, "code": code}} if code else None
    return cls(message, response=response, body=body)


def _ok(content: str = None):
    from types import SimpleNamespace

    content = content or json.dumps({"items": [{
        "name": "хлеб", "readout": "estimate",
        "per100g": {"kcal": 250, "protein": 8, "fat": 3, "carbs": 48},
    }]})
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason="stop"
        )],
        usage=None,
    )


class ScriptedTransport:
    """Отвечает по сценарию, СВОЕМУ на каждую модель, и считает вызовы.

    Сценарий на модель, а не общая очередь: весь смысл теста в том, КАКАЯ цель
    получила запрос, и общая очередь этот факт как раз бы и скрыла.
    """

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        queue = self.script.get(kwargs["model"], [])
        step = queue.pop(0) if queue else _ok()
        if isinstance(step, BaseException):
            raise step
        return step

    def models_called(self) -> list[str]:
        return [c["model"] for c in self.calls]


@pytest.fixture
def transport(monkeypatch):
    """Ставит фейковый транспорт, ключ-заглушку и убирает паузы backoff.

    `_retry_delay` подменяется нулём, а не патчится `asyncio.sleep`: пауза — это
    поведение, которое тестируется отдельно (`test_retry_delay_*`), а здесь она
    только замедляла бы прогон.
    """
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    monkeypatch.setattr(llm_mod, "_retry_delay", lambda exc, attempt: 0.0)
    monkeypatch.setattr(llm_mod, "_demoted_model", None, raising=False)
    monkeypatch.setattr(llm_mod, "_last_switch_alert", None, raising=False)

    def install(script: dict[str, list]) -> ScriptedTransport:
        from types import SimpleNamespace

        tr = ScriptedTransport(script)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=tr))
        )
        monkeypatch.setattr(llm_mod, "_client_for", lambda target: client)
        return tr

    return install


def _primary() -> str:
    return settings.llm_vision_model


def _fallback() -> str:
    return settings.llm_fallback_vision_model


# ════════════════════ §8.3 — классификация отказов ═══════════════════════════
# Классификатор — отдельная функция, а не цепочка `except` по месту вызова,
# именно чтобы это правило можно было проверить без сети и без провайдера.


@pytest.mark.parametrize("exc, expected", [
    (_http_error(NotFoundError, 404, "model_not_found"), "model"),
    (_http_error(NotFoundError, 404), "model"),
    (_http_error(RateLimitError, 429, "rate_limit_exceeded"), "transient"),
    (_http_error(InternalServerError, 503), "transient"),
    (APITimeoutError(request=_REQUEST), "transient"),
    (APIConnectionError(request=_REQUEST), "transient"),
    (_http_error(AuthenticationError, 401, "invalid_api_key"), "account"),
    (_http_error(RateLimitError, 429, "insufficient_quota"), "account"),
    (_http_error(AuthenticationError, 403), "account"),
    (_http_error(NotFoundError, 400, message="image too large"), "user"),
])
def test_classify_failure_matrix(exc, expected):
    assert llm_mod.classify_failure(exc) == expected


def test_only_model_level_failures_are_switchable():
    """Суть сужения матрицы: аккаунт и пользователь резервом не лечатся."""
    assert "model" in llm_mod.SWITCHABLE and "transient" in llm_mod.SWITCHABLE
    assert "account" not in llm_mod.SWITCHABLE
    assert "user" not in llm_mod.SWITCHABLE
    assert "vendor" not in llm_mod.SWITCHABLE


def test_insufficient_quota_is_account_despite_429_status():
    """429 несёт два разных смысла, и различить их можно только по коду.

    Обычный rate limit модели лечится сменой модели; исчерпанная квота аккаунта —
    нет, потому что аккаунт общий. Если бы классификатор смотрел только на статус,
    исчерпанная квота уводила бы разбор на резерв и добавляла 20 с перед тем же
    отказом.
    """
    quota = _http_error(RateLimitError, 429, "insufficient_quota")
    plain = _http_error(RateLimitError, 429, "rate_limit_exceeded")
    assert llm_mod.classify_failure(quota) == "account"
    assert llm_mod.classify_failure(plain) == "transient"


# ════════════════════ §8.3 — переключение на вторичный таргет ════════════════


async def test_model_not_found_switches_without_retry(transport):
    """Снятая модель → резерв, БЕЗ ретрая: повтор к несуществующей модели бесполезен."""
    tr = transport({_primary(): [_http_error(NotFoundError, 404, "model_not_found")]})

    items = await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary(), _fallback()]   # ровно одна попытка на каждой
    assert items and FLAG_FALLBACK_PROVIDER in items[0].warnings


async def test_401_does_not_switch(transport):
    """401 — аккаунтный отказ: резерв ходит с тем же ключом (ADR-0018 §2.2)."""
    tr = transport({_primary(): [_http_error(AuthenticationError, 401, "invalid_api_key")]})

    with pytest.raises(llm_mod.LLMError):
        await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary()]        # вторичный НЕ пробовали
    assert _fallback() not in tr.models_called()


async def test_insufficient_quota_does_not_switch(transport):
    """`insufficient_quota` — та же квота у той же организации."""
    tr = transport({_primary(): [_http_error(RateLimitError, 429, "insufficient_quota")]})

    with pytest.raises(llm_mod.LLMRateLimitError):
        await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary()]


async def test_user_level_400_does_not_switch(transport):
    """Кривой запрос другой моделью не выпрямится."""
    tr = transport({_primary(): [_http_error(NotFoundError, 400, message="image too large")]})

    with pytest.raises(llm_mod.LLMError):
        await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary()]


async def test_429_retries_then_switches(transport):
    """DoD п.4: 429 → `llm_retry_429` попыток на модели, ТОЛЬКО потом смена цели.

    Порядок именно такой (ADR-0018 §2.3): транзиентный лимит проходит сам, и
    уводить разбор на другую модель раньше времени значит платить за резерв там,
    где хватило бы паузы.
    """
    rate = lambda: _http_error(RateLimitError, 429, "rate_limit_exceeded")  # noqa: E731
    attempts = settings.llm_retry_429 + 1        # первая попытка + ретраи
    tr = transport({_primary(): [rate() for _ in range(attempts)]})

    items = await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary()] * attempts + [_fallback()]
    assert FLAG_FALLBACK_PROVIDER in items[0].warnings


async def test_5xx_retries_once_then_switches(transport):
    """5xx — один ретрай без задержки, потом резерв."""
    tr = transport({_primary(): [_http_error(InternalServerError, 502),
                                 _http_error(InternalServerError, 502)]})

    await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary(), _primary(), _fallback()]


async def test_failure_on_both_targets_is_honest_error(transport):
    """Вендорский отказ поперёк обеих целей → честная ошибка, не третья попытка."""
    tr = transport({
        _primary(): [_http_error(InternalServerError, 502)] * 2,
        _fallback(): [_http_error(InternalServerError, 502)] * 2,
    })

    with pytest.raises(llm_mod.LLMError):
        await LLMExtractor().extract(text="хлеб")

    assert tr.models_called().count(_fallback()) == 2


async def test_finish_reason_length_retries_with_doubled_budget(transport):
    """DoD п.4: обрезка по бюджету → ретрай ×2 НА ТОЙ ЖЕ модели.

    Смена цели здесь ничего не чинит: другая модель того же вендора вернёт ответ
    такой же длины. Тест фиксирует, что переключения не происходит.
    """
    from types import SimpleNamespace

    truncated = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{"),
                                 finish_reason="length")],
        usage=None,
    )
    tr = transport({_primary(): [truncated, _ok()]})

    await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary(), _primary()]
    budget_key = settings.llm_max_tokens_param
    assert tr.calls[1][budget_key] == tr.calls[0][budget_key] * 2


async def test_successful_primary_never_flags_fallback(transport):
    """Плашка резерва не появляется, когда резерв не работал."""
    tr = transport({})
    items = await LLMExtractor().extract(text="хлеб")

    assert tr.models_called() == [_primary()]
    assert FLAG_FALLBACK_PROVIDER not in items[0].warnings


async def test_timeout_is_passed_to_sdk(transport):
    """§8.1: таймаут отдаём SDK, а не оборачиваем в `asyncio.wait_for`.

    Обёртка бросила бы задачу и оставила висящий сокет; SDK рвёт соединение.
    """
    tr = transport({})
    await LLMExtractor().extract(text="хлеб")

    assert tr.calls[0]["timeout"] == settings.llm_timeout_s


async def test_switch_alerts_admin_once_per_day(transport, monkeypatch):
    """Первое за сутки переключение → алерт; последующие — только лог (ADR-0018 §5).

    Без дедупликации всплеск 5xx превратил бы алерт в спам, после которого его
    перестают читать, — то есть вернул бы немой отказ другим путём.
    """
    sent: list[str] = []

    async def hook(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(llm_mod, "_alert_hook", hook, raising=False)
    tr = transport({_primary(): [_http_error(NotFoundError, 404, "model_not_found")] * 2})

    await LLMExtractor().extract(text="хлеб")
    await LLMExtractor().extract(text="хлеб")

    assert tr.models_called().count(_fallback()) == 2   # переключились оба раза
    assert len(sent) == 1                               # а написали один
    assert _fallback() in sent[0]


async def test_account_failure_alerts_admin(transport, monkeypatch):
    """Аккаунтный отказ резервом не лечится — тем более обязан быть слышен."""
    sent: list[str] = []

    async def hook(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(llm_mod, "_alert_hook", hook, raising=False)
    transport({_primary(): [_http_error(AuthenticationError, 401, "invalid_api_key")]})

    with pytest.raises(llm_mod.LLMError):
        await LLMExtractor().extract(text="хлеб")

    assert len(sent) == 1 and "account" in sent[0]


# ════════════════════ §8.2 — пауза перед повтором 429 ════════════════════════


def test_retry_delay_grows_and_respects_retry_after():
    """`Retry-After` главнее собственного backoff: провайдер знает своё окно."""
    plain = _http_error(RateLimitError, 429, "rate_limit_exceeded")
    assert 1.0 <= llm_mod._retry_delay(plain, 0) <= 1.5
    assert 4.0 <= llm_mod._retry_delay(plain, 1) <= 4.5

    with_header = _http_error(RateLimitError, 429, "rate_limit_exceeded")
    with_header.response.headers["retry-after"] = "12"
    assert llm_mod._retry_delay(with_header, 0) == 12.0


def test_retry_delay_has_jitter():
    """Два параллельных батча (§5.2) стартуют одновременно; без jitter они и
    повторяли бы одновременно, воспроизводя тот самый лимит."""
    exc = _http_error(RateLimitError, 429, "rate_limit_exceeded")
    delays = {llm_mod._retry_delay(exc, 0) for _ in range(20)}
    assert len(delays) > 1


# ════════════════════ §8.4 — guard присутствия модели ════════════════════════


class _FakeModels:
    def __init__(self, ids=None, error: BaseException | None = None) -> None:
        self.ids = ids or []
        self.error = error

    async def list(self, **kwargs):
        from types import SimpleNamespace

        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self.ids])


@pytest.fixture
def guard_env(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    monkeypatch.setattr(llm_mod, "_demoted_model", None, raising=False)

    def install(ids=None, error=None):
        from types import SimpleNamespace

        client = SimpleNamespace(models=_FakeModels(ids, error))
        monkeypatch.setattr(llm_mod, "_client_for", lambda target: client)
        return client

    return install


async def test_guard_passes_when_model_present(guard_env):
    guard_env(ids=[settings.llm_vision_model, settings.llm_fallback_vision_model])
    assert await llm_mod.check_models() is True
    assert llm_mod.default_targets()[0].model == settings.llm_vision_model


async def test_guard_demotes_and_alerts_when_model_gone(guard_env, monkeypatch):
    """Сценарий 17.07.26: модель снята. Guard увидел бы это за месяц до отказа.

    Побочный эффект намеренный: модель уходит с первого места, то есть следующий
    разбор не потратит попытку на заведомо мёртвую цель.
    """
    sent: list[str] = []

    async def hook(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(llm_mod, "_alert_hook", hook, raising=False)
    guard_env(ids=[settings.llm_fallback_vision_model])

    assert await llm_mod.check_models() is False
    assert llm_mod.default_targets()[0].model == settings.llm_fallback_vision_model
    assert len(sent) == 1 and settings.llm_vision_model in sent[0]


async def test_guard_demotion_does_not_drop_primary(guard_env):
    """Демоция — не удаление: guard мог ошибиться, и первичная остаётся последней."""
    guard_env(ids=[settings.llm_fallback_vision_model])
    await llm_mod.check_models()

    models = [t.model for t in llm_mod.default_targets()]
    assert models == [settings.llm_fallback_vision_model, settings.llm_vision_model]


async def test_guard_restores_primary_when_model_returns(guard_env):
    guard_env(ids=[settings.llm_fallback_vision_model])
    await llm_mod.check_models()
    guard_env(ids=[settings.llm_vision_model])
    await llm_mod.check_models()

    assert llm_mod.default_targets()[0].model == settings.llm_vision_model


async def test_guard_network_failure_does_not_demote_or_raise(guard_env):
    """DoD п.5: сеть недоступна → guard молчит и НЕ трогает порядок целей.

    «Модель отсутствует» и «спросить не удалось» — разные события. Свести их в
    один bool значило бы демотировать рабочую модель при каждом сетевом сбое, а
    бот, не поднявшийся из-за недоступного `/v1/models`, хуже бота с неизвестным
    статусом модели.
    """
    guard_env(error=APIConnectionError(request=_REQUEST))

    assert await llm_mod.run_startup_guard() is None
    assert llm_mod.default_targets()[0].model == settings.llm_vision_model


async def test_guard_without_key_is_silent(monkeypatch):
    """Ключа нет → расчёт КБЖУ и так недоступен; guard об этом не сообщает."""
    monkeypatch.setattr(settings, "llm_api_key", None)
    assert await llm_mod.run_startup_guard() is None


async def test_daily_guard_job_runs_once_per_interval(guard_env, monkeypatch):
    """Планировщик тикает раз в минуту — job обязана держать свой интервал сама."""
    guard_env(ids=[settings.llm_vision_model])
    calls: list[int] = []

    async def counted(**kwargs):
        calls.append(1)
        return True

    monkeypatch.setattr(llm_mod, "check_models", counted)
    job = llm_mod.make_llm_model_guard(interval_s=3600.0)

    await job()
    await job()
    await job()

    assert len(calls) == 1


# ════════════════════ SecretStr + утечка ключа в трейсбек ════════════════════


def test_config_validation_error_does_not_contain_key_value():
    """DoD п.6 — регресс утечки 02.08.2026 в `journalctl`.

    Дословный сценарий: волна 1 удалила поля `GROQ_*` из кода, прод-`.env` их
    сохранил, `extra=forbid` уронил процесс на импорте — и pydantic напечатал
    `input_value='gsk_...'`. Ключ ушёл в журнал открытым текстом, а `Restart=always`
    превратил это в краш-луп, который выглядит как `active (running)`.

    Тест намеренно проверяет ЛИШНЕЕ поле, а не объявленное: SecretStr его закрыть
    не может (у лишнего поля нет типа), и утёк именно такой ключ.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            bot_token="x", database_url="sqlite://",
            GROQ_API_KEY="gsk_SUPERSECRET_VALUE",
        )

    text = str(excinfo.value)
    assert "GROQ_API_KEY" in text          # имя поля остаётся: без него не починить
    assert "gsk_SUPERSECRET_VALUE" not in text
    assert "input_value" not in text


def test_secret_fields_are_masked_in_repr_and_dump():
    """Второй путь утечки: объект настроек в логе или в дампе.

    `hide_input_in_errors` его не закрывает — там ошибки нет вовсе.
    """
    cfg = Settings(bot_token="TG-SECRET", database_url="sqlite://",
                   llm_api_key="sk-SECRET")

    assert "TG-SECRET" not in repr(cfg)
    assert "sk-SECRET" not in repr(cfg)
    assert "sk-SECRET" not in str(cfg.model_dump())
    # …и при этом остаётся доступным там, где действительно нужен.
    assert cfg.llm_api_key.get_secret_value() == "sk-SECRET"


def test_missing_key_still_fails_loudly(monkeypatch):
    """SecretStr не должен превратить «ключа нет» в «ключ есть, но пустой».

    `SecretStr("")` — истинный объект, поэтому наивное `if not settings.llm_api_key`
    после перехода на SecretStr молча пропускало бы пустой ключ дальше, в SDK.
    """
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(""))
    with pytest.raises(llm_mod.LLMNotConfiguredError):
        llm_mod.default_target()

    monkeypatch.setattr(settings, "llm_api_key", None)
    with pytest.raises(llm_mod.LLMNotConfiguredError):
        llm_mod.default_target()


def test_fallback_target_differs_only_by_model():
    """Вариант B: второго кодового пути не возникает (драйвер D4 ADR-0018).

    Если у резерва однажды заведётся свой ключ или свой base_url, это перестанет
    быть «та же семья моделей» и потребует пересмотра матрицы §8.3.
    """
    import dataclasses

    primary = llm_mod.LLMTarget(base_url="u", api_key="k", model="a")
    fields = {f.name for f in dataclasses.fields(llm_mod.LLMTarget)}
    assert {"role", "timeout_s"} <= fields

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(settings, "llm_api_key", SecretStr("test-key"))
        p, f = llm_mod.default_target(), llm_mod.fallback_target()

    differing = {
        name for name in vars(p)
        if getattr(p, name) != getattr(f, name)
    }
    assert differing == {"model", "role"}
    assert primary.role == "primary"


async def test_no_live_provider_calls_in_pytest():
    """GAP-6: транспорт всегда фейковый — этот файл не ходит в сеть.

    Проверяется структурно: без ключа и без подмены клиента прод-путь падает
    раньше любого сетевого вызова.
    """
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(settings, "llm_api_key", None)
        with pytest.raises(llm_mod.LLMNotConfiguredError):
            await LLMExtractor().extract(text="хлеб")
    assert asyncio.get_running_loop() is not None


# ════════════ Раннер eval: где он ОБЯЗАН расходиться с продом ════════════════
# Раннер ходит прод-путём — в этом весь смысл замера. Но два параметра блока 11
# на нём работают против измерения, и оба поймались только при попытке запустить
# регресс (2026-08-03), уже после того, как код был написан.


def test_eval_target_does_not_inherit_prod_timeout():
    """Прод-таймаут 20 с выбран под медиану победителя 1.3 с.

    Раннер меряет и заведомо медленные модели: у `qwen/qwen3.6-27b` замерено
    21.9 с на вызов (`llm-eval-results-2026-07-27.md` §4). С прод-таймаутом её
    прогон превратился бы в сплошные ошибки — то есть замер «доказал» бы
    негодность модели свойством инструмента, а не данными. Ровно тот класс
    дефекта, что уже стоил проекту четырёх итераций замера OFF.
    """
    from tests.eval.run_eval import EVAL_TIMEOUT_S

    assert EVAL_TIMEOUT_S > settings.llm_timeout_s * 5
    # И это не «без таймаута»: висящий сокет всё равно обязан рваться.
    assert EVAL_TIMEOUT_S < float("inf")


def test_eval_target_disables_client_side_429_retries():
    """У раннера свой backoff (10·2^n) и своя метрика латентности.

    Ретраи внутри клиента спят ВНУТРИ `extract()`, то есть попадают в
    `latency_s` — метрику, по которой принимается инкремент («медианная
    задержка ≤2.0 с»). Двойной ретрай завышал бы её молча.
    """
    import inspect

    from tests.eval import run_eval

    src = inspect.getsource(run_eval.run_model)
    assert "retry_429=0" in src
    assert "timeout_s=EVAL_TIMEOUT_S" in src


async def test_target_retry_429_overrides_settings(transport, monkeypatch):
    """`LLMTarget.retry_429=0` действительно отключает ретраи, а не игнорируется."""
    monkeypatch.setattr(settings, "llm_retry_429", 5)
    tr = transport({"m": [_http_error(RateLimitError, 429, "rate_limit_exceeded")]})
    target = llm_mod.LLMTarget(base_url="u", api_key="k", model="m", retry_429=0)

    with pytest.raises(llm_mod.LLMRateLimitError):
        await llm_mod.LLMClient(target=target).complete(system="s", user_content="u")

    assert len(tr.calls) == 1          # ни одного повтора, несмотря на settings=5


async def test_explicit_target_never_falls_back(transport):
    """Раннер измеряет КОНКРЕТНУЮ модель: молчаливый уход на другую испортил бы
    ровно тот замер, ради которого он запущен."""
    tr = transport({"m": [_http_error(NotFoundError, 404, "model_not_found")]})
    target = llm_mod.LLMTarget(base_url="u", api_key="k", model="m")

    with pytest.raises(llm_mod.LLMError):
        await llm_mod.LLMClient(target=target).complete(system="s", user_content="u")

    assert tr.models_called() == ["m"]
    assert settings.llm_fallback_vision_model not in tr.models_called()
