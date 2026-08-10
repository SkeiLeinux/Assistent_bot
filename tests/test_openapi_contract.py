"""tests/test_openapi_contract.py — сверка `docs/api/openapi.yaml` с кодом (блок 14).

Зачем файл существует. `openapi.yaml` — производный контракт (SSOT — ТЗ), и за всю
волну 1 он отстал ЦЕЛИКОМ: ни `unresolved`, ни `warnings`, ни `alternative` в нём не
появилось. Отстал он молча, потому что синхронность документа не проверял никто.
Разовая синхронизация это не чинит — через один флаг всё повторится. Поэтому здесь
не «проверка, что файл красивый», а два пина, каждый из которых обязан падать:

1. **Набор кодов `warnings`** в контракте == набор констант `FLAG_*` в коде.
   Именно этот тест при написании и нашёл, что кодов не 10 (как в задании), а 11:
   `portion_missing` живёт в `services/food_service.py` и в списке отсутствовал.
2. **`ProvenanceSource` и обязательность КБЖУ** — НЕ менялись (ТЗ §6.6, §12.5 п.8).
   Отказ модели выражается структурой ответа (`unresolved`), а не nullable-ккал;
   тест стоит против «заодно причесать».

Правило проекта (`llm-eval-dataset.md` §3D.1): проверка без негативного контроля не
считается введённой. Поэтому обе сверки вынесены в чистые функции, и на них прогнаны
мутанты — набор с лишним кодом, набор с недостающим (`test_*_detects_drift`).
Без этого «зелёный» тест не отличается от отсутствующего.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "docs" / "api" / "openapi.yaml"
NUTRITION = ROOT / "app" / "contexts" / "nutrition"

# Флаги, которые НИКОГДА не попадают в `warnings` позиции: живут только в строке
# лога (§9). Явный список, а не «пропустим неизвестное»: новый `FLAG_*` в любом
# модуле обязан быть либо в контракте, либо здесь — то есть требует решения, а не
# молчаливо расходится с фронтом.
LOG_ONLY_FLAGS = {
    "json_invalid",  # extractor: ответ не разобрался, позиций нет вовсе
}

# Пин ADR-0013. Значения не менялись ни разу за волну 1 — и не должны были:
# «чисел нет» не ложится в ось происхождения чисел (ТЗ §6.6 п.2).
PROVENANCE_SOURCE = ["label", "off", "web", "nutritionix", "estimate", "db", "manual"]

# КБЖУ в сохраняемой записи обязательны. Nullable здесь означал бы, что запись с
# пустыми числами — законные данные; она портит итоги дня, прогресс и дайджест.
MACRO_FIELDS = ["kcal", "protein", "fat", "carbs"]


@pytest.fixture(scope="module")
def spec() -> dict:
    """DoD п.1: файл обязан быть валидным YAML. Парсер — уже в зависимостях."""
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


# ── сбор кодов флагов из кода ────────────────────────────────────────────────

def _flag_constants(path: Path) -> dict[str, str]:
    """Все `FLAG_* = "литерал"` модуля. Через AST, а не импортом.

    Импорт потянул бы `app.config` со своими требованиями к окружению, а нам нужен
    ровно текст константы. Заодно тест остаётся честным, если модуль не грузится.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith("FLAG_"):
                out[target.id] = node.value.value
    return out


def _code_flag_codes() -> dict[str, set[str]]:
    """Коды флагов из кода: {'wire': достигают REST, 'log_only': только в логе}."""
    found: dict[str, str] = {}
    for module in sorted(NUTRITION.rglob("*.py")):
        for name, value in _flag_constants(module).items():
            found[value] = name
    wire = {code for code in found if code not in LOG_ONLY_FLAGS}
    return {"wire": wire, "log_only": set(found) & LOG_ONLY_FLAGS}


def _spec_warning_codes(spec: dict) -> set[str]:
    return set(spec["components"]["schemas"]["NutritionWarning"]["enum"])


def _drift(code: set[str], contract: set[str]) -> tuple[set[str], set[str]]:
    """(есть в коде, нет в контракте) / (есть в контракте, нет в коде)."""
    return code - contract, contract - code


# ── 1. warnings: контракт == код ─────────────────────────────────────────────

def test_warning_codes_match_code(spec):
    missing, stale = _drift(_code_flag_codes()["wire"], _spec_warning_codes(spec))
    assert not missing, f"флаги есть в коде, но не описаны в openapi.yaml: {sorted(missing)}"
    assert not stale, f"флаги описаны в openapi.yaml, но в коде их нет: {sorted(stale)}"


def test_log_only_flags_are_not_in_contract(spec):
    """Обратная сторона списка: лог-флаг не должен утечь во фронтовый enum.

    Иначе фронт нарисует пользователю текст под код, который до него не доходит.
    """
    leaked = _spec_warning_codes(spec) & LOG_ONLY_FLAGS
    assert not leaked, f"лог-флаги в контракте фронта: {sorted(leaked)}"


def test_log_only_list_is_not_stale():
    """Пин самого списка исключений: `json_invalid` обязан существовать в коде.

    Список исключений — тоже гипотеза. Если константу переименовали, а строку
    здесь забыли, набор `wire` молча вырастет на невидимый код.
    """
    assert _code_flag_codes()["log_only"] == LOG_ONLY_FLAGS


def test_warning_drift_is_detectable(spec):
    """Негативный контроль: сверка обязана падать в ОБЕ стороны.

    Зелёный тест на верных данных не доказывает, что он что-то ловит: ровно так
    prod-`select_best` годами «проходил» docstring, который не выполнял.
    """
    contract = _spec_warning_codes(spec)
    assert contract, "enum NutritionWarning пуст — сверять нечего"
    dropped = sorted(contract)[0]

    missing, stale = _drift(contract, contract - {dropped})
    assert missing == {dropped} and not stale

    missing, stale = _drift(contract, contract | {"flag_from_the_future"})
    assert stale == {"flag_from_the_future"} and not missing


# ── 2. что менять было нельзя ────────────────────────────────────────────────

def test_provenance_source_unchanged(spec):
    assert spec["components"]["schemas"]["ProvenanceSource"]["enum"] == PROVENANCE_SOURCE


def test_macros_are_not_nullable(spec):
    """§6.6: частичный успех выражается структурой (`unresolved`), а не null-ами."""
    for schema_name in ("FoodItemDraft", "DishIngredient", "AlternativeNutrition"):
        props = spec["components"]["schemas"][schema_name]["properties"]
        # FoodItemDraft несёт проектные имена макросов, остальные — короткие «как на проводе».
        fields = MACRO_FIELDS if schema_name == "FoodItemDraft" else ("kcal", "p", "f", "c")
        for field in fields:
            assert not props[field].get("nullable"), (
                f"{schema_name}.{field} стал nullable — обязательность КБЖУ нарушена"
            )


def test_unresolved_carries_no_macros(spec):
    """`unresolved` — позиции БЕЗ чисел. Появление там КБЖУ вернуло бы nullable-путь."""
    props = spec["components"]["schemas"]["UnresolvedItem"]["properties"]
    assert not set(props) & set(MACRO_FIELDS)
    assert props["reason"]["enum"] == ["unreadable", "no_source"]


# ── 3. структурная целостность документа (DoD п.1) ───────────────────────────

def test_analyze_returns_unresolved_with_200(spec):
    """Пустой items при непустом unresolved — 200, не 502 (§6.6)."""
    ok = spec["paths"]["/nutrition/analyze"]["post"]["responses"]["200"]
    schema = ok["content"]["application/json"]["schema"]
    assert "unresolved" in schema["properties"]
    assert schema["properties"]["unresolved"]["items"]["$ref"].endswith("/UnresolvedItem")


def test_image_cap_matches_config(spec):
    """Кап ввода в контракте == `api_max_images` в конфиге (ТЗ §5.2).

    Литерал `5` тут пережил и правку конфига, и вычистку хардкодов из LLM-слоя.
    """
    from app.config import Settings

    cap = Settings.model_fields["api_max_images"].default
    for path in ("/nutrition/analyze", "/nutrition/foods/analyze"):
        body = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]
        assert body["schema"]["properties"]["images"]["maxItems"] == cap, path


def test_health_schema_matches_the_endpoint(spec):
    """Тот же пин, что и на `warnings`, но для `/health` (задача смоука §1).

    Документ отстал на всю волну 1 именно потому, что синхронность никто не
    проверял. Новый публичный эндпоинт заводится сразу с пином, а не «синхронизируем
    потом»: сверка в обе стороны ловит и забытое поле, и поле-призрак.
    """
    from app.delivery.miniapp_api.health import HEALTH_FIELDS

    schema = spec["components"]["schemas"]["Health"]
    assert set(schema["properties"]) == set(HEALTH_FIELDS)
    assert set(schema["required"]) == set(HEALTH_FIELDS)


def test_health_is_the_only_path_without_auth(spec):
    """`security: []` — публичная поверхность, и она обязана быть ровно одна.

    Пин стоит против «заодно откроем ещё один путь для удобства»: список
    исключений в коде (`PUBLIC_API_PATHS`) и в контракте должны совпадать.
    """
    from app.delivery.miniapp_api.web import PUBLIC_API_PATHS

    public = {
        f"/api/v1{path}"
        for path, ops in spec["paths"].items()
        for op in ops.values()
        if isinstance(op, dict) and op.get("security") == []
    }
    assert public == set(PUBLIC_API_PATHS)


def test_every_ref_resolves(spec):
    """Все `$ref` указывают на существующий узел: битая ссылка ломает генераторы."""

    def refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    yield value
                else:
                    yield from refs(value)
        elif isinstance(node, list):
            for value in node:
                yield from refs(value)

    for ref in set(refs(spec)):
        assert ref.startswith("#/"), f"внешняя ссылка не поддерживается: {ref}"
        node = spec
        for part in ref[2:].split("/"):
            assert part in node, f"битый $ref: {ref}"
            node = node[part]
