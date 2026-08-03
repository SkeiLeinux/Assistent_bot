"""Блок 13 — наблюдаемость LLM-слоя (ТЗ контракта §9).

Предмет один: **отказ обязан быть заметен**. Блок 11 построил механизм
(ретраи, резервная цель, guard присутствия модели), но узнать, что он сработал —
или что он срабатывает ПОСТОЯННО, — было нечем. Ровно эта слепота (D12) сделала
немым отказ 17.07.26: модель сняли, `APIError` замаскировался под «сервис
временно недоступен», и разница между «сегодня не повезло» и «расчёт КБЖУ мёртв
неделю» не была видна ниоткуда.

Отсюда состав файла:

  - **состав полей** — пин по СПИСКУ ключей, а не по подстроке: тест по подстроке
    пережил бы удаление поля, а поля §9 выбраны под конкретные режимы отказа
    (немое снятие модели, тихая деградация на резерв, обрезка по `length`, сдвиг
    `readout`), и вычистить их как «лишние» нельзя;
  - **негативный контроль** — ни ключа, ни ответа модели, ни текста пользователя
    в логе. Без него правило «ключ не логируется» — комментарий, а не гарантия:
    ключ уже утекал в `journalctl` 02.08.26, а старый обработчик схемы печатал
    сырой ответ модели целиком, то есть дневник питания живых людей;
  - **частота строк** — одна на вызов и одна на разбор. Тарелка из шести позиций
    не должна давать простыню: в простыне распределение `readout`/`sources`
    приходится складывать глазами, а именно его сдвиг и есть сигнал.

Живых вызовов провайдера нет (GAP-6) — транспорт фейковый.
"""

import json
import logging

import pytest
from pydantic import SecretStr

import app.contexts.nutrition.services.llm_service as llm_mod
from app.config import settings
from app.contexts.nutrition.resolve import prompts
from app.contexts.nutrition.resolve.extractor import (
    FLAG_JSON_INVALID,
    LLMExtractor,
    path_of,
)
from app.contexts.nutrition.services import observability as obs

# Фикстура транспорта и сборщик успешного ответа берутся из тестов блока 11, а
# не копируются: две копии одного фейка расходятся, и тогда «наблюдаемость
# проверена» будет означать «проверена на транспорте, которого нет в других
# тестах».
from tests.test_llm_resilience import _http_error, _ok, transport  # noqa: F401


# ── Вспомогательное ──────────────────────────────────────────────────────────

def _answer(*items) -> str:
    return json.dumps({"items": list(items)})


def _item(name="хлеб", readout="estimate", **extra) -> dict:
    body = {
        "name": name,
        "readout": readout,
        "per100g": {"kcal": 250, "protein": 8, "fat": 3, "carbs": 48},
    }
    body.update(extra)
    return body


def _lines(caplog, event: str) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith(f"{event} ")]


def _keys(line: str) -> list[str]:
    """Ключи строки `k=v` по порядку. Значения не разбираем — предмет теста ключи."""
    return [chunk.split("=", 1)[0] for chunk in line.split(" ")[1:]]


# ════════════════════ Состав полей §9 ════════════════════════════════════════
# §9 ТЗ — SSOT формата. Список ниже продублирован ДОСЛОВНО из него намеренно:
# сверять `LLM_CALL_FIELDS` с самим собой бессмысленно, нужна вторая, независимо
# записанная копия — она и ломается, когда поле уходит из кода.

_SPEC_9_LLM_CALL = [
    "model", "target", "path", "batch", "latency_ms", "prompt_tokens",
    "completion_tokens", "finish_reason", "n_items", "readout", "flags", "retries",
]
_SPEC_9_RESOLVE = ["sources", "total_ms", "budget_exhausted"]


def test_llm_call_line_carries_every_field_of_spec_9():
    line = obs.llm_call_line(
        model="gpt-5.4-mini", target="primary", path=obs.PATH_ANALYZE_PHOTO,
        batch="1/2", latency_ms=1300,
        usage={"prompt_tokens": 1802, "completion_tokens": 274},
        finish_reason="stop", n_items=3,
        readout={"label": 2, "estimate": 1, "unreadable": 0},
        flags=["arith_mismatch"], retries=0,
    )
    assert _keys(line) == _SPEC_9_LLM_CALL


def test_resolve_line_carries_every_field_of_spec_9():
    line = obs.resolve_line(
        path=obs.PATH_ANALYZE_PHOTO, n_items=4, unresolved=1,
        sources={"label": 2, "off": 1, "estimate": 1},
        total_ms=2400, budget_exhausted=False,
    )
    assert set(_SPEC_9_RESOLVE) <= set(_keys(line))


def test_builder_rejects_missing_and_extra_fields():
    """Негативный контроль строгости билдера.

    Без него пин выше проверял бы только то, что полей не ДОБАВЛЯЛИ: строка,
    собранная из неполного словаря, молча вышла бы короче, а тест на состав
    ключей — если бы кто-то ослабил его до `<=` — этого не заметил.
    """
    full = dict.fromkeys(obs.LLM_CALL_FIELDS, 0)

    with pytest.raises(ValueError, match="нет"):
        obs.format_event("llm_call", obs.LLM_CALL_FIELDS,
                         {k: v for k, v in full.items() if k != "retries"})
    with pytest.raises(ValueError, match="лишние"):
        obs.format_event("llm_call", obs.LLM_CALL_FIELDS, {**full, "prompt": "..."})


def test_readout_counters_print_all_three_values():
    """Все три значения, включая нулевые: строка с переменным набором ключей не
    грепается и не сравнивается между днями — а сравнение между днями и есть
    единственный способ увидеть сдвиг распределения."""
    line = obs.llm_call_line(
        model="m", target="primary", path=obs.PATH_ANALYZE_TEXT, batch="1/1",
        latency_ms=1, usage=None, finish_reason="stop", n_items=0,
        readout=obs.readout_counts([]), flags=[], retries=0,
    )
    assert "readout={label:0,estimate:0,unreadable:0}" in line


def test_missing_usage_is_a_dash_not_a_zero():
    """«Провайдер не сообщил usage» ≠ «потрачено 0 токенов»: ноль в цене — это
    утверждение, и ложное."""
    line = obs.llm_call_line(
        model="m", target="primary", path=obs.PATH_ANALYZE_TEXT, batch="1/1",
        latency_ms=1, usage=None, finish_reason=None, n_items=1,
        readout=obs.readout_counts([]), flags=[], retries=0,
    )
    assert "prompt_tokens=-" in line and "completion_tokens=-" in line


# ════════════════════ Пути разбора (§4.3) ════════════════════════════════════


def test_path_of_covers_all_five_entry_points():
    """Пять путей ТЗ §4.3, а не четыре режима промпта: дневник обслуживает и
    текст, и фото, а в логе у них разная цена, латентность и режимы отказа."""
    assert path_of(prompts.MODE_DIARY, False) == obs.PATH_ANALYZE_TEXT
    assert path_of(prompts.MODE_DIARY, True) == obs.PATH_ANALYZE_PHOTO
    assert path_of(prompts.MODE_DISH_TEXT, False) == obs.PATH_DISH_TEXT
    assert path_of(prompts.MODE_DISH_PHOTOS, True) == obs.PATH_DISH_PHOTOS
    assert path_of(prompts.MODE_DISH_COMBINED, True) == obs.PATH_DISH_COMBINED


def test_dish_mode_names_equal_path_names():
    """Пин на совпадение словарей.

    `FoodService` пишет строку разбора по имени РЕЖИМА (он не импортирует
    `resolve`, границы ADR-0004), а извлекатель — по имени ПУТИ. Пока строки
    совпадают, лог однороден; разъедутся — в логе появятся два имени одного
    пути, и группировка по `path` тихо развалится на две половины.
    """
    from app.contexts.nutrition.services import food_service as fs

    assert (fs.MODE_DISH_TEXT, fs.MODE_DISH_PHOTOS, fs.MODE_DISH_COMBINED) == (
        obs.PATH_DISH_TEXT, obs.PATH_DISH_PHOTOS, obs.PATH_DISH_COMBINED
    )


# ════════════════════ Частота строк ══════════════════════════════════════════


async def test_one_line_per_call_not_per_item(transport, caplog):  # noqa: F811
    """DoD п.4: три позиции в одном ответе → ОДНА строка, а не три.

    Обратное (строка на позицию) выглядело бы полнее и было бы бесполезно:
    тарелка из шести продуктов даёт простыню, в которой распределение `readout`
    приходится складывать глазами.
    """
    transport({settings.llm_vision_model: [_ok(_answer(
        _item("гречка"), _item("курица"), _item("хлеб", readout="label"),
    ))]})

    with caplog.at_level(logging.INFO):
        items = await LLMExtractor().extract(text="гречка, курица, хлеб")

    assert len(items) == 3
    calls = _lines(caplog, obs.EVENT_LLM_CALL)
    assert len(calls) == 1
    assert "n_items=3" in calls[0]
    assert "readout={label:1,estimate:2,unreadable:0}" in calls[0]


async def test_batches_give_one_line_each(transport, monkeypatch, caplog):  # noqa: F811
    """Батчи (§5.2) — единственный законный способ получить больше одной строки
    на разбор, и номер батча обязан быть в строке: без него всплеск латентности
    на разборе из десяти снимков неотличим от медленной модели."""
    monkeypatch.setattr(settings, "llm_max_images", 1)
    tr = transport({settings.llm_vision_model: [
        _ok(_answer(_item("а"))), _ok(_answer(_item("б"))), _ok(_answer(_item("в"))),
    ]})

    with caplog.at_level(logging.INFO):
        await LLMExtractor().extract(images=[b"1", b"2", b"3"])

    calls = _lines(caplog, obs.EVENT_LLM_CALL)
    assert len(calls) == len(tr.calls) == 3
    assert sorted(c.split("batch=")[1].split(" ")[0] for c in calls) == [
        "1/3", "2/3", "3/3"
    ]


async def test_failed_parse_is_logged_and_retry_is_visible(transport, caplog):  # noqa: F811
    """Ретрай на невалидный JSON пишется отдельной строкой.

    Иначе сдвиг формата ответа — отказ, который не даёт исключения и на который
    никто не жалуется, потому что второй вызов чинит его в 90% случаев — был бы
    невидим ровно до дня, когда чинить перестанет.
    """
    transport({settings.llm_vision_model: [_ok("не json"), _ok(_answer(_item()))]})

    with caplog.at_level(logging.INFO):
        await LLMExtractor().extract(text="хлеб")

    calls = _lines(caplog, obs.EVENT_LLM_CALL)
    assert len(calls) == 2
    assert f"flags=[{FLAG_JSON_INVALID}]" in calls[0] and "n_items=0" in calls[0]
    assert "n_items=1" in calls[1]


async def test_fallback_target_is_visible_in_the_line(transport, caplog):  # noqa: F811
    """Тихая деградация на резерв — главный адресат §9.

    Качество на резерве по ADR-0018 не падает (паритет 92%/83%), поэтому
    пользователь НЕ пожалуется, и рост доли `target=fallback` — единственный
    признак того, что первичная модель умирает.
    """
    from openai import NotFoundError

    transport({settings.llm_vision_model: [
        _http_error(NotFoundError, 404, "model_not_found")
    ]})

    with caplog.at_level(logging.INFO):
        await LLMExtractor().extract(text="хлеб")

    call = _lines(caplog, obs.EVENT_LLM_CALL)[0]
    assert "target=fallback" in call
    assert f"model={settings.llm_fallback_vision_model}" in call
    switch = _lines(caplog, obs.EVENT_SWITCH)
    assert switch and f"to_model={settings.llm_fallback_vision_model}" in switch[0]
    assert "alerted=true" in switch[0]


async def test_second_switch_of_the_day_is_logged_as_not_alerted(transport, caplog):  # noqa: F811
    """`alerted` — факт, а не предсказание.

    Алерт дедуплицируется («первое за сутки», ADR-0018 §5), поэтому по алертам
    нельзя отличить единичный сбой от постоянной работы на резерве. Именно
    поэтому строка нужна на КАЖДОЕ переключение — и именно поэтому она обязана
    честно говорить, дошло ли уведомление до человека.
    """
    from openai import NotFoundError

    gone = lambda: _http_error(NotFoundError, 404, "model_not_found")  # noqa: E731
    transport({settings.llm_vision_model: [gone(), gone()]})

    with caplog.at_level(logging.INFO):
        await LLMExtractor().extract(text="хлеб")
        caplog.clear()
        await LLMExtractor().extract(text="сыр")

    assert "alerted=false" in _lines(caplog, obs.EVENT_SWITCH)[0]


async def test_one_resolve_line_per_analyze(session, caplog):
    """DoD п.4, вторая половина: разбор целиком — одна строка, не по позиции."""
    from tests.test_nutrition import FakeExtractor, PassthroughResolver, _svc

    svc = _svc(session, extractor=FakeExtractor(), resolver=PassthroughResolver())

    with caplog.at_level(logging.INFO):
        result = await svc.analyze_text("гречка 200 г, курица 150 г")

    lines = _lines(caplog, obs.EVENT_RESOLVE)
    assert len(result.items) == 2
    assert len(lines) == 1
    assert f"path={obs.PATH_ANALYZE_TEXT}" in lines[0]
    assert "sources={estimate:2}" in lines[0] and "budget_exhausted=false" in lines[0]


async def test_resolve_line_reports_budget_exhaustion(session, caplog):
    """`budget_exhausted` отличает «источники промолчали» от «мы до них не дошли».

    Без флага деградация лестницы по бюджету выглядит в логе как здоровый
    `estimate` — то есть как норма.
    """
    from tests.test_nutrition import FakeExtractor, PassthroughResolver, _svc

    resolver = PassthroughResolver()
    resolver.total_budget_s = -1.0        # дедлайн в прошлом: сеть запрещена сразу
    svc = _svc(session, extractor=FakeExtractor(), resolver=resolver)

    with caplog.at_level(logging.INFO):
        await svc.analyze_text("гречка")

    assert "budget_exhausted=true" in _lines(caplog, obs.EVENT_RESOLVE)[0]


# ════════════════════ Негативный контроль: что НЕ попадает в лог ═════════════
# DoD п.3. Без этих двух тестов запреты §1.4 — комментарии, а не гарантии.

_SECRET = "sk-proj-DO-NOT-LOG-0123456789"
_PRIVATE = "борщ по рецепту тёщи"


def _all_log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


async def test_key_and_answer_never_reach_the_log(transport, monkeypatch, caplog):  # noqa: F811
    """Ни ключа, ни его хвоста, ни содержимого ответа модели.

    Ключ утекал в `journalctl` 02.08.26 через печать входного значения pydantic;
    ответ модели — это дневник питания двух живых людей, и попадать в лог он не
    должен ни целиком, ни фрагментом.
    """
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(_SECRET))
    transport({settings.llm_vision_model: [
        _ok(_answer(_item(_PRIVATE, note="съедено на ужин")))
    ]})

    with caplog.at_level(logging.DEBUG):
        await LLMExtractor().extract(text=f"я съел {_PRIVATE}")

    text = _all_log_text(caplog)
    assert _SECRET not in text and _SECRET[-8:] not in text
    assert _PRIVATE not in text and "борщ" not in text
    assert "ужин" not in text
    # Контроль самого контроля: строки-то писались, иначе тест зелен ни о чём.
    assert _lines(caplog, obs.EVENT_LLM_CALL)


async def test_schema_failure_does_not_dump_the_raw_answer(transport, caplog):  # noqa: F811
    """Тот же запрет на пути, где он раньше НАРУШАЛСЯ.

    Обработчик `ValidationError` печатал и `str(e)`, и сырой ответ целиком.
    Убрать один `raw` было бы недостаточно: pydantic v2 включает `input_value`
    в текст самой ошибки, поэтому «не логировать raw» без замены на коды ошибок
    осталось бы иллюзией — сохранная половина утечки.
    """
    broken = _answer({"name": _PRIVATE, "readout": "label"})   # нет per100g → схема
    transport({settings.llm_vision_model: [_ok(broken), _ok(broken)]})

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(llm_mod.LLMError):
            await LLMExtractor().extract(text="что-то")

    text = _all_log_text(caplog)
    assert _PRIVATE not in text and "борщ" not in text
    # Диагностика при этом обязана остаться: без указания правила отказ схемы
    # неотличим от «модель вернула мусор», и чинить его нечем.
    assert "схема не принята" in text and "per100g" in text


def test_ports_validator_messages_carry_no_model_data():
    """Условие, при котором `_schema_errors` имеет право печатать `msg`.

    Тексты валидаторов `ports.py` уходят в лог целиком, поэтому интерполировать
    в них поля позиции нельзя: `name`, `note` и `brand` — это то, что человек
    съел и как он это записал. Проверка структурная, а не «прочитал и убедился»:
    следующий валидатор с f-строкой по свободному полю упадёт на pytest, а не
    утечёт в `journalctl`. Закрытые словари (`readout`) интерполировать можно —
    у них конечное множество значений, объявленное в этом же файле.
    """
    import inspect
    import re

    from app.contexts.nutrition.resolve import ports

    src = inspect.getsource(ports)
    raises = re.findall(r"raise ValueError\(f?[\"'](.*?)[\"']\)", src)
    assert raises, "не нашли ни одного validator-сообщения — регулярка протухла"
    for message in raises:
        for free_text_field in ("self.name", "self.note", "self.brand"):
            assert free_text_field not in message, (
                f"сообщение валидатора уходит в лог целиком: {message}"
            )


def test_builder_flattens_anything_that_looks_like_free_text():
    """Последний рубеж: даже переданный по ошибке текст не станет текстом в логе.

    Запреты выше держатся на том, что вызывающие передают счётчики. Этот тест —
    про форму, а не про дисциплину: билдер режет всё за пределами короткого
    безопасного алфавита, поэтому фраза не выживет и при ошибке вызывающего.
    """
    line = obs.llm_call_line(
        model=_PRIVATE, target="primary", path="p", batch="1/1",
        latency_ms=1, usage=None, finish_reason="stop", n_items=0,
        readout={}, flags=[], retries=0,
    )
    assert "борщ" not in line
    # И строка осталась разбираемой: одно поле — один токен без пробелов.
    assert len(line.split(" ")) == len(obs.LLM_CALL_FIELDS) + 1


# ════════════════════ Guard: три статуса, не два ═════════════════════════════


async def test_guard_logs_unknown_when_provider_is_unreachable(monkeypatch, caplog):
    """«Спросить не удалось» ≠ «модели нет» (ADR-0018 §2.4).

    В логе они обязаны различаться так же, как в коде: иначе сетевой сбой
    читается как снятие модели, то есть как инцидент 17.07.26.
    """
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))

    async def unreachable(target):
        return None

    monkeypatch.setattr(llm_mod, "list_models", unreachable)
    with caplog.at_level(logging.INFO):
        assert await llm_mod.check_models(alert=False) is None

    line = _lines(caplog, obs.EVENT_GUARD)[0]
    assert f"status={obs.GUARD_UNKNOWN}" in line


async def test_guard_logs_missing_when_model_is_gone(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    monkeypatch.setattr(llm_mod, "_demoted_model", None, raising=False)

    async def without_primary(target):
        return {"some-other-model"}

    monkeypatch.setattr(llm_mod, "list_models", without_primary)
    with caplog.at_level(logging.INFO):
        assert await llm_mod.check_models(alert=False) is False

    line = _lines(caplog, obs.EVENT_GUARD)[0]
    assert f"status={obs.GUARD_MISSING}" in line
    assert f"model={settings.llm_vision_model}" in line
