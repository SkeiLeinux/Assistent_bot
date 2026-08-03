"""services/observability.py — структурированные строки лога LLM-слоя (ТЗ §9).

**Зачем этот файл вообще есть.** Блок 11 построил отказоустойчивость: ретраи,
резервную цель, guard присутствия модели. Но узнать, что всё это СРАБОТАЛО — или
что оно срабатывает постоянно, — было нечем. Ровно эта слепота (D12) сделала
немым отказ 17.07.26: вендор снял модель, `APIError` замаскировался под «сервис
временно недоступен», и разница между «сегодня не повезло» и «расчёт КБЖУ мёртв
неделю» не была видна ниоткуда.

**Набор полей §9 выбран не «на всякий случай», а под конкретные режимы отказа** —
не вычищать как лишние:

  - `target`   — тихая деградация на резерв: качество не падает (паритет 92%/83%),
                 поэтому пользователь НЕ пожалуется, и рост доли `fallback` —
                 единственный признак, что первичная модель умирает;
  - `retries`  — рост ретраев предшествует отказу, а не следует за ним;
  - `finish_reason` — обрезка по `length` неотличима от «модель вернула не JSON»
                 без этого поля (пользователь видит одно и то же сообщение);
  - `readout`  — сдвиг распределения `label→estimate` означает, что модель
                 перестала читать этикетки, при полностью исправном транспорте;
  - `flags`    — срабатывания проверок §6, включая C3a (кДж-утечка F-01);
  - токены     — фактическая цена без раннера.

**Два запрета, оба под тестом (`tests/test_llm_observability.py`):**

1. **Ключ не логируется никогда** — ни целиком, ни хвостом. Это тот же класс, что
   утечка `GROQ_API_KEY` в `journalctl` 02.08.26.
2. **Ни текста пользователя, ни ответа модели.** Это дневник питания двух живых
   людей; в лог идут только счётчики и коды закрытых словарей.

Оба обеспечиваются формой, а не дисциплиной: строку собирает ОДИН билдер, он
принимает только объявленные поля, и каждое значение проходит через `_scalar`,
который режет всё за пределами короткого безопасного алфавита. Свободному тексту
попасть в строку физически неоткуда.

**Почему модуль лежит в `services`, а не в `resolve`.** Строки пишут обе стороны:
извлекатель (`resolve/extractor.py`, строка на вызов) и `FoodService` (строка на
разбор). Контракт import-linter «resolve — leaf внутри Nutrition» разрешает
`resolve → services` и запрещает обратное, поэтому общий модуль обязан лежать
внизу — здесь, рядом с `llm_service`.
"""

from __future__ import annotations

import re

# ── Имена событий и состав полей (ТЗ §9, дословно) ───────────────────────────
# Состав — кортеж, а не свободный набор kwargs: билдер требует ВСЕ поля и
# отвергает незнакомые. Иначе следующая правка добавила бы поле в один из пяти
# путей, и строки разъехались бы между путями незаметно.

EVENT_LLM_CALL = "llm_call"
EVENT_RESOLVE = "llm_resolve"

LLM_CALL_FIELDS: tuple[str, ...] = (
    "model", "target", "path", "batch", "latency_ms", "prompt_tokens",
    "completion_tokens", "finish_reason", "n_items", "readout", "flags", "retries",
)

RESOLVE_FIELDS: tuple[str, ...] = (
    "path", "n_items", "unresolved", "sources", "total_ms", "budget_exhausted",
)

# Две точки §1.3, у которых алерт админу уже есть с блока 11: переключение на
# резерв и результат guard'а. Строка нужна рядом с алертом, а не вместо него —
# алерт дедуплицируется («первое за сутки»), и без лога второе, третье и
# сто первое переключение того же дня не остались бы нигде.
EVENT_SWITCH = "llm_switch"
SWITCH_FIELDS: tuple[str, ...] = ("from_model", "to_model", "failure", "alerted")

EVENT_GUARD = "llm_guard"
GUARD_FIELDS: tuple[str, ...] = ("model", "status")

# Статусы guard'а. Три, а не два: «спросить не удалось» — это НЕ «модели нет»
# (ADR-0018 §2.4), и сведение их в один bool демотировало бы рабочую модель на
# каждом сетевом сбое.
GUARD_PRESENT = "present"
GUARD_MISSING = "missing"
GUARD_UNKNOWN = "unknown"

# ── Пути разбора (ТЗ §4.3) ───────────────────────────────────────────────────
# Пять точек входа, а не четыре режима промпта: дневниковый режим `diary`
# обслуживает и текст, и фото, а в логе их надо различать — у них разная цена,
# разная латентность и разные режимы отказа.

PATH_ANALYZE_TEXT = "analyze_text"
PATH_ANALYZE_PHOTO = "analyze_photo"
PATH_DISH_TEXT = "dish_text"
PATH_DISH_PHOTOS = "dish_photos"
PATH_DISH_COMBINED = "dish_combined"

PATHS: tuple[str, ...] = (
    PATH_ANALYZE_TEXT, PATH_ANALYZE_PHOTO,
    PATH_DISH_TEXT, PATH_DISH_PHOTOS, PATH_DISH_COMBINED,
)

# Значения `readout` (ТЗ §3.1) — печатаются ВСЕ три, включая нулевые: строка с
# переменным набором ключей не грепается и не сравнивается между днями.
READOUTS: tuple[str, ...] = ("label", "estimate", "unreadable")

# Алфавит скаляра. Всё остальное заменяется на «_»: имена моделей содержат
# `/`, `.`, `-`, коды флагов — подчёркивания, и этого достаточно. Пробелы,
# кавычки и кириллица не проходят по построению — то есть ни фраза пользователя,
# ни строка из ответа модели в лог не попадут, даже если кто-то передаст их сюда.
_UNSAFE = re.compile(r"[^0-9A-Za-z_.:/+-]")
_MAX_SCALAR = 64

_NONE = "-"


def _scalar(value) -> str:
    """Одно значение → безопасный короткий токен."""
    if value is None:
        return _NONE
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        value = f"{value:.3f}".rstrip("0").rstrip(".") or "0"
    text = _UNSAFE.sub("_", str(value))
    return text[:_MAX_SCALAR] if text else _NONE


def _render(value) -> str:
    """Значение поля → фрагмент строки. Словарь и список — в форме §9."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{_scalar(k)}:{_scalar(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return "[" + ",".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


def format_event(event: str, fields: tuple[str, ...], values: dict) -> str:
    """`event k=v k=v ...` строго по объявленному составу полей.

    Требует ВСЕ поля и отвергает незнакомые. Это не педантизм: тест приёмки
    сверяет ключи строки со списком §9, и без строгости здесь он проверял бы
    только то, что поля не добавляли, — а вычистить поле как «лишнее» можно было
    бы молча.
    """
    missing = [f for f in fields if f not in values]
    extra = [k for k in values if k not in fields]
    if missing or extra:
        raise ValueError(
            f"{event}: состав полей не совпадает с §9 "
            f"(нет: {missing}, лишние: {extra})"
        )
    return f"{event} " + " ".join(f"{f}={_render(values[f])}" for f in fields)


# ── Счётчики: единственная форма, в которой содержимое ответа попадает в лог ──

def readout_counts(items) -> dict[str, int]:
    """`{label:2,estimate:1,unreadable:0}` — распределение по позициям ответа."""
    counts = dict.fromkeys(READOUTS, 0)
    for item in items or ():
        key = getattr(item, "readout", None)
        if key in counts:
            counts[key] += 1
    return counts


def flag_codes(items) -> list[str]:
    """Уникальные коды флагов §6 по всем позициям, в порядке первого появления.

    Коды, а не тексты: словарь закрыт (`resolve/checks.py`), и именно поэтому
    флаги можно писать в лог, не нарушая запрет на содержимое ответа.
    """
    seen: dict[str, None] = {}
    for item in items or ():
        for flag in getattr(item, "warnings", ()) or ():
            seen[str(flag)] = None
    return list(seen)


def source_counts(items) -> dict[str, int]:
    """`{label:2,off:1,estimate:1}` — провенанс разрешённых позиций (§9)."""
    counts: dict[str, int] = {}
    for item in items or ():
        key = getattr(item, "source", None) or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def usage_tokens(usage: dict | None) -> tuple[int | None, int | None]:
    """(prompt_tokens, completion_tokens) из usage провайдера; None — не сообщил."""
    if not isinstance(usage, dict):
        return None, None
    def get(key):
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else None
    return get("prompt_tokens"), get("completion_tokens")


# ── Готовые строки ───────────────────────────────────────────────────────────

def llm_call_line(
    *,
    model, target, path, batch, latency_ms, usage, finish_reason,
    n_items, readout, flags, retries,
) -> str:
    """Строка на ОДИН вызов провайдера (§9)."""
    prompt_tokens, completion_tokens = usage_tokens(usage)
    return format_event(EVENT_LLM_CALL, LLM_CALL_FIELDS, {
        "model": model,
        "target": target,
        "path": path,
        "batch": batch,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
        "n_items": n_items,
        "readout": readout,
        "flags": flags,
        "retries": retries,
    })


def switch_line(*, from_model, to_model, failure, alerted) -> str:
    """Строка на переключение первичной цели на резервную (§1.3)."""
    return format_event(EVENT_SWITCH, SWITCH_FIELDS, {
        "from_model": from_model, "to_model": to_model,
        "failure": failure, "alerted": alerted,
    })


def guard_line(*, model, status) -> str:
    """Строка на результат проверки присутствия модели (§1.3, ADR-0018 §2.4)."""
    return format_event(EVENT_GUARD, GUARD_FIELDS, {"model": model, "status": status})


def resolve_line(
    *, path, n_items, unresolved, sources, total_ms, budget_exhausted
) -> str:
    """Строка на РАЗБОР целиком (§9): сколько позиций, откуда числа, за сколько."""
    return format_event(EVENT_RESOLVE, RESOLVE_FIELDS, {
        "path": path,
        "n_items": n_items,
        "unresolved": unresolved,
        "sources": sources,
        "total_ms": total_ms,
        "budget_exhausted": budget_exhausted,
    })
