"""tests/eval/run_eval.py — прогон eval-датасета по кандидату (ТЗ §6). НЕ pytest.

Отдельный скрипт, а не тест: делает живые сетевые вызовы и логирует цену — в общем
`pytest`-прогоне ему не место. Гоняет ярус A `manifest.yaml` через ТОТ ЖЕ путь, что
прод (`LLMExtractor`), меняя лишь цель (провайдер/модель) — то есть раннер является
первым потребителем абстракции ADR-0017 §6 и способом проверить, что она годная,
до перевода прода.

Запуск (из корня репо):
    python -m tests.eval.run_eval run                       # дефолт = прод-цель
    python -m tests.eval.run_eval run --provider openai     --model gpt-5.4
    python -m tests.eval.run_eval run --provider openrouter --model google/gemma-4-31b-it:free --limit 5
    python -m tests.eval.run_eval report      # свести все runs/*.json в markdown

Дефолт раннера СОВПАДАЕТ с прод-целью (`openai` / `gpt-5.4-mini`, ADR-0017 §16) —
это не удобство, а предусловие: пока прод и раннер смотрят в разные модели, любая
правка промпта настраивается вслепую (ТЗ контракта §0.1).

Метрики и пороги — docs/05_implementation/llm-eval-dataset.md §4 (SSOT).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

# Запуск как `python tests/eval/run_eval.py` не кладёт корень репо в sys.path —
# добавляем сами, чтобы `import app...` работал в обоих режимах (-m и файлом).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows-консоль по умолчанию cp1251 и падает на ×/≤/рамках. Переводим вывод в
# UTF-8 (errors=replace на всякий случай), чтобы прогресс печатался, а не ронял прогон.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from app.config import settings  # noqa: E402
from app.contexts.nutrition.resolve.extractor import LLMExtractor  # noqa: E402
from app.contexts.nutrition.services.llm_service import (  # noqa: E402
    LLMError,
    LLMRateLimitError,
    LLMTarget,
)

EVAL_DIR = Path(__file__).resolve().parent
MANIFEST = EVAL_DIR / "manifest.yaml"
RUNS_DIR = EVAL_DIR / "runs"

# ── Реестр провайдеров (ADR-0017 §6/§8) ──────────────────────────────────────
# base_url + откуда ключ + дефолты на провайдера. Прежний прод-провайдер убран
# вместе с зависимостью (ADR-0017 §16): его vision-модель была в статусе preview
# и дала в замере 76% kcal-hit при 21.9 с на вызов. Его ключ из конфига тоже
# удалён, поэтому запись здесь всё равно перестала бы работать.


class Provider:
    def __init__(self, base_url, key_attr, reasoning_effort="",
                 max_images=5, extra_headers=None, max_tokens_param="max_tokens",
                 send_response_format=True):
        self.base_url = base_url
        self.key_attr = key_attr
        self.reasoning_effort = reasoning_effort
        self.max_images = max_images
        self.extra_headers = extra_headers or {}
        self.max_tokens_param = max_tokens_param
        self.send_response_format = send_response_format

    def key(self):
        return getattr(settings, self.key_attr, None)


DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.4-mini"

PROVIDERS = {
    "openrouter": Provider(
        "https://openrouter.ai/api/v1", "openrouter_api_key",
        reasoning_effort="", max_images=5,
        extra_headers={
            "HTTP-Referer": "https://github.com/SkeiLeinux/lifeManager_bot",
            "X-Title": "lifeManager-bot eval",
        },
    ),
    "openai": Provider(
        # Прод-провайдер (ADR-0017 §16). Параметры обязаны совпадать с config.py:
        # llm_base_url / llm_max_images / llm_max_tokens_param / llm_reasoning_effort.
        # Расхождение = раннер меряет не то, что поедет в прод (ТЗ контракта §0.1);
        # сходимость проверяется тестом test_eval_default_matches_prod_target.
        "https://api.openai.com/v1", "openai_api_key",
        # GPT-5.x: max_tokens отвергается → max_completion_tokens. reasoning не шлём:
        # замер показал вред (100% → 94/88% при ×3 выходных токенов и ×2 задержке).
        reasoning_effort="", max_images=5,
        max_tokens_param="max_completion_tokens",
    ),
    "anthropic": Provider(
        # OpenAI-совместимый эндпоинт Anthropic. Особенности (проверено 2026-07-27):
        # max_tokens (не max_completion_tokens); response_format json_object отвергает
        # (хочет json_schema) → не слать, _extract_json снимает ```-обёртку; vision и
        # reasoning_effort=low принимаются. reasoning по умолчанию не шлём ("").
        "https://api.anthropic.com/v1", "claude_api_key",
        reasoning_effort="", max_images=5, max_tokens_param="max_tokens",
        send_response_format=False,
    ),
}

# Тариф $/1M токенов (вход, выход). 0 = бесплатно на этом тире. None-ключ (нет в
# словаре) → цену не считаем, показываем только токены. Значения GPT — из
# ADR-0017 §8/G; уточнить по факту, если OpenAI сменит прайс.
TARIFFS = {
    # Провайдер ушёл (ADR-0017 §16), но тариф оставлен: `report` сводит и старые
    # прогоны из runs/, а без записи они показали бы «—» вместо честного нуля.
    "qwen/qwen3.6-27b": (0.0, 0.0),   # был бесплатный тир
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.6-luna": (1.0, 6.0),
    "claude-haiku-4-5": (1.0, 5.0),   # ADR-0017 §8/G
    "claude-sonnet-4-6": (3.0, 15.0),  # оценка (типичный прайс Sonnet); уточнить
    # gpt-5.4-mini / gpt-5.4 / gpt-5.6-terra — цену подставит С. по токенам
    # openrouter *:free — 0
}

# Прод сжимает фото на клиенте перед отправкой (api.js fileToCompressedDataUrl):
# длинная сторона ≤1280, JPEG q=82. Раннер обязан делать то же — иначе кормит
# модель сырьём, которого прод никогда не шлёт, и упирается в 413 на больших PNG.
PROD_MAX_SIDE = 1280
PROD_JPEG_QUALITY = 82

KCAL_HIT_TOL = 0.02   # §4: |kcal_модели − эталон| / эталон ≤ 2%
BJU_HIT_TOL = 0.02    # §4: то же по каждому из Б/Ж/У
DIAG_BAND = 0.10      # диагностический более широкий коридор (не порог приёмки)

# Стоп-слова, чтобы токен-оверлап name/brand не срабатывал на служебных словах.
_STOP = {"с", "и", "в", "на", "по", "из", "для", "мясной", "продукт",
         "пищевой", "вид", "виде", "вкусом", "со", "the", "of"}


# ── Загрузка датасета ────────────────────────────────────────────────────────

def load_cases(tier: str | None) -> list[dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    cases = [c for c in data if isinstance(c, dict) and "id" in c]
    if tier:
        cases = [c for c in cases if c.get("tier") == tier]
    # Из прогона исключаются два вида кейсов — оба остаются в манифесте, потому что
    # эталон дорого восстанавливать, а причина исключения важна как запись:
    #   missing_photo — снимка нет (продукт надо переснять);
    #   unscorable    — снимок есть, но эталон из него НЕ выводится (продиктован
    #                   извне, т.к. этикетка физически нечитаема). Мерить на таком
    #                   точность чтения бессмысленно: это тест человека, не модели.
    # Держать их в знаменателе нельзя — они одинаково занижают всех и маскируют
    # реальную разницу между моделями.
    def skip_reason(c: dict) -> str | None:
        if c.get("status") in ("missing_photo", "unscorable"):
            return c["status"]
        return None if c.get("image") else "missing_photo"

    skipped = [(c["id"], skip_reason(c)) for c in cases if skip_reason(c)]
    if skipped:
        print("Исключены из прогона: "
              + ", ".join(f"{i} ({r})" for i, r in skipped))
    return [c for c in cases if not skip_reason(c)]


def check_desync(cases: list[dict], records: list[dict]) -> list[dict]:
    """Детектор рассинхрона «фото ↔ эталон» (добавлен 2026-07-27 по факту инцидента).

    Зачем. В первом замере 11 из 28 кейсов яруса A имели снимок от ДРУГОЙ записи
    манифеста: при ручной дедупликации поехала нумерация. Метрики этого не видят —
    для них «модель ошиблась» и «эталон не от этого фото» неразличимы, поэтому
    отчёты бодро сообщили «порог не прошёл никто», хотя лидеры давали 94–100%.
    Атуотер тоже не помог: числа эталона верны сами по себе, они просто относятся
    к другому продукту. Нужен независимый признак — и он есть.

    Признак. Если модель на снимке X читает продукт, чей ЭТАЛОН лежит в записи Y,
    это свидетельство о содержимом снимка, а не об ошибке модели. Проверяем две
    независимые подписи:
      • имя  — совпадение названия с эталоном чужого кейса лучше, чем со своим;
      • числа — kcal мимо своего эталона, но попадает в чужой с точностью ≤2%.
    Срабатывание по обеим — почти наверняка рассинхрон. Только по числам бывает
    совпадением (два продукта с близкой калорийностью), поэтому это «слабый» сигнал.

    Ограничение: одна модель может ошибиться и «увидеть» чужой продукт. Настоящую
    силу признак набирает на нескольких прогонах — согласие разных вендоров на одном
    и том же чужом продукте случайностью не объясняется.
    """
    by_id = {c["id"]: c for c in cases}
    alerts = []
    for rec in records:
        case = by_id.get(rec["id"])
        if not case:
            continue
        items = [r.get("items") for r in rec.get("runs", []) if r.get("items")]
        if not items:
            continue
        item = items[0][0]
        own_gt = case["ground_truth"]
        own_tok = _tokens(own_gt.get("name"))
        got_tok = _tokens(item.get("name"))
        mk = (item.get("per100g") or {}).get("kcal")

        def jac(a, b):
            return len(a & b) / len(a | b) if (a | b) else 0.0

        own_name = jac(got_tok, own_tok)
        try:
            own_kcal_rel = abs(float(mk) - gt_per100g(own_gt)["kcal"]) / gt_per100g(own_gt)["kcal"]
        except (TypeError, ValueError, ZeroDivisionError):
            own_kcal_rel = None

        best_name, best_name_id = own_name, None
        best_kcal_id = None
        for other in cases:
            if other["id"] == case["id"]:
                continue
            o_gt = other["ground_truth"]
            s = jac(got_tok, _tokens(o_gt.get("name")))
            if s > best_name + 0.05:
                best_name, best_name_id = s, other["id"]
            try:
                o_k = gt_per100g(o_gt)["kcal"]
                if mk is not None and o_k and abs(float(mk) - o_k) / o_k <= KCAL_HIT_TOL:
                    if own_kcal_rel is None or own_kcal_rel > 0.20:
                        best_kcal_id = other["id"]
            except (TypeError, ValueError, ZeroDivisionError):
                continue

        if best_name_id or best_kcal_id:
            alerts.append({
                "id": case["id"],
                "image": case.get("image"),
                "saw": item.get("name"),
                "expected": own_gt.get("name"),
                "name_match": best_name_id,
                "kcal_match": best_kcal_id,
                "strong": bool(best_name_id and best_kcal_id
                               and best_name_id == best_kcal_id),
            })
    return alerts


def print_desync(alerts: list[dict]) -> None:
    if not alerts:
        print("\n✅ Рассинхрон «фото ↔ эталон» не обнаружен.")
        return
    strong = [a for a in alerts if a["strong"]]
    print(f"\n{'!' * 70}")
    print(f"🔴 ПОДОЗРЕНИЕ НА РАССИНХРОН ФОТО ↔ ЭТАЛОН: {len(alerts)} кейсов "
          f"(уверенно: {len(strong)})")
    print("Метрики ниже НЕДОСТОВЕРНЫ, пока это не разобрано: модель штрафуется")
    print("за верно прочитанную этикетку, если эталон приписан не к тому снимку.")
    print("!" * 70)
    for a in sorted(alerts, key=lambda x: not x["strong"]):
        tag = "УВЕРЕННО" if a["strong"] else "слабый сигнал"
        tgt = a["name_match"] or a["kcal_match"]
        print(f"  [{tag}] {a['id']} ({a['image']})")
        print(f"      модель читает : {a['saw']}")
        print(f"      эталон говорит: {a['expected']}")
        print(f"      похоже на эталон кейса {tgt}"
              f"{'  (совпали и название, и kcal)' if a['strong'] else ''}")
    print("\nЧто делать: сверить снимки глазами; если подтвердилось — обменять файлы\n"
          "местами (эталон обычно верен, разъезжаются именно файлы) и перепрогнать.\n")


def gt_per100g(gt: dict) -> dict:
    """Эталон в единицах на 100 г. per_portion пересчитываем через portion_grams."""
    basis = gt.get("basis", "per100g")
    keys = ("kcal", "protein", "fat", "carbs")
    if basis == "per_portion":
        pg = gt.get("portion_grams")
        if not pg:
            raise ValueError("per_portion без portion_grams — нельзя привести к 100 г")
        factor = 100.0 / float(pg)
        return {k: (None if gt.get(k) is None else float(gt[k]) * factor) for k in keys}
    return {k: (None if gt.get(k) is None else float(gt[k])) for k in keys}


def prep_image(path: Path) -> bytes:
    """Повторяет клиентское сжатие прода (api.js): EXIF-ориентация (браузер
    авто-разворачивает по умолчанию), длинная сторона ≤1280, JPEG q=82, RGB."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # как canvas в браузере
    img = img.convert("RGB")            # JPEG без альфы
    w, h = img.size
    scale = min(1.0, PROD_MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=PROD_JPEG_QUALITY)
    return buf.getvalue()


def _tokens(s: str | None) -> set[str]:
    if not s:
        return set()
    out = set()
    for w in "".join(ch.lower() if ch.isalnum() else " " for ch in s).split():
        if len(w) >= 3 and w not in _STOP:
            out.add(w)
    return out


# ── Метрики яруса A (§4) ──────────────────────────────────────────────────────

def score_case(case: dict, item: dict | None) -> dict:
    """Метрики одного кейса. item — первая позиция ответа (per100g), None если провал."""
    gt = case["ground_truth"]
    g100 = gt_per100g(gt)
    m = {"json_valid": item is not None}
    if item is None:
        return m

    p = item.get("per100g", {})
    mk = p.get("kcal")
    # kcal / БЖУ hit по строгому порогу §4
    def rel(model_v, gt_v):
        if gt_v is None or model_v is None or gt_v == 0:
            return None
        return abs(float(model_v) - float(gt_v)) / abs(float(gt_v))

    m["kcal_rel"] = rel(mk, g100["kcal"])
    m["kcal_hit"] = m["kcal_rel"] is not None and m["kcal_rel"] <= KCAL_HIT_TOL
    m["kcal_diag"] = m["kcal_rel"] is not None and m["kcal_rel"] <= DIAG_BAND

    bju_hits, bju_total = 0, 0
    for k in ("protein", "fat", "carbs"):
        r = rel(p.get(k), g100[k])
        if r is None:
            continue
        bju_total += 1
        if r <= BJU_HIT_TOL:
            bju_hits += 1
    m["bju_hits"], m["bju_total"] = bju_hits, bju_total

    # кДж-конфузия: kcal завышен в ~4.18× (диапазон 3.8–4.5) — грубая ошибка (§4)
    if mk is not None and g100["kcal"]:
        ratio = float(mk) / float(g100["kcal"])
        m["kj_confusion"] = 3.8 <= ratio <= 4.5
    else:
        m["kj_confusion"] = False

    # База per100g: осмысленно только на ловушке per-portion. Если модель выдала
    # per100g близко к «сырым» числам за порцию — перепутала базу.
    if gt.get("basis") == "per_portion" and mk is not None and gt.get("kcal"):
        m["base_confused"] = abs(float(mk) - float(gt["kcal"])) / float(gt["kcal"]) <= 0.10
    else:
        m["base_confused"] = False

    # Извлечение name/brand (эвристика — токен-оверлап; §4 «пригодно для поиска»).
    m["name_ok"] = bool(_tokens(item.get("name")) & _tokens(gt.get("name")))
    if gt.get("brand"):
        m["brand_ok"] = bool(_tokens(item.get("brand")) & _tokens(gt.get("brand")))
    else:
        m["brand_ok"] = None  # эталонного бренда нет — не оцениваем
    return m


# ── Метрики ярусов B и C (§3B.6, §3C.4) ──────────────────────────────────────
# Заведены ДО данных — порядок из §7b: инструмент проверки первым. Обратный
# порядок на ярусе A стоил 11 рассинхронных кейсов. Каждая метрика ниже имеет
# негативный контроль в tests/eval/test_scoring.py; по правилу §3D.1 метрика без
# него не считается введённой, поэтому добавлять сюда что-то без мутанта нельзя.
#
# Путь ПРОГОНА ярусов B/C (какие вызовы делать, в каком режиме) приходит вместе
# с данными — §7b шаги 3–4. Здесь только оценка уже полученного ответа, и она
# отделена от сети намеренно: так метрики тестируемы без единого живого вызова.

MACROS = ("kcal", "protein", "fat", "carbs")

C_PER100G_TOL = 0.02      # C3: тот же порог, что на ярусе A — это те же этикетки
C_TOTAL_TOL = 0.005       # C4: 0.5%, не 5% — код считает точно, допуск на округление
C_WEIGHT_MISMATCH = 0.30  # C6: порог расхождения «заявленный вес vs Σ граммовок»


def _norm(s: str | None) -> str:
    """Нормализация имени для сопоставления: регистр + пунктуация + пробелы."""
    if not s:
        return ""
    return " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in s).split()
    )


def _matches(model_name: str | None, names: list[str]) -> bool:
    """Сопоставление имени позиции с именем/синонимами компонента.

    Сначала точное совпадение нормализованных строк, затем — пересечение токенов
    (модель говорит «пюре картофельное», разметка «картофельное пюре»). Синонимы
    объявляются в кейсе (§3B.5): без них метрика мерила бы лексику, а не сегментацию.
    """
    got = _norm(model_name)
    if not got:
        return False
    for n in names:
        if got == _norm(n):
            return True
    got_tok = _tokens(model_name)
    return any(got_tok & _tokens(n) for n in names if _tokens(n))


def _component_names(comp: dict) -> list[str]:
    return [comp.get("name", "")] + list(comp.get("synonyms") or [])


def _cv(values: list[float]) -> float | None:
    """Коэффициент вариации. None, если считать не на чем."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return (var ** 0.5) / abs(mean)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def _run_kcal_signature(items: list[dict]) -> float:
    """Суммарный per100g.kcal ответа — величина для B6.

    Почему именно она: по контракту на тарелке `portion_g = null` (§3B.1), а
    граммовки есть только у взвешенной пятёрки. Значит «kcal ответа» в смысле
    итога не определён на 10 кейсах из 15, и разброс между прогонами измерим
    только по тому, что модель реально выдаёт — per100g позиций.
    """
    total = 0.0
    for it in items:
        v = (it.get("per100g") or {}).get("kcal")
        if v is not None:
            total += float(v)
    return total


def score_b_case(case: dict, runs: list[list[dict] | None]) -> dict:
    """Метрики яруса B по одному кейсу (§3B.6).

    `runs` — ответы по повторным прогонам: список позиций либо None, если ответ
    не распарсился/не прошёл схему. Первый прогон считается основным (B2–B5, B8),
    B6/B7 смотрят на все.
    """
    m: dict = {"b1_json_valid": all(r is not None for r in runs) and bool(runs)}
    first = runs[0] if runs and runs[0] is not None else None
    if first is None:
        return m

    # B2: на кадре без упаковки readout="label" — самозаявление, которое нечем
    # подтвердить. Метрика имеет смысл только при package_in_frame=false.
    m["b2_no_label"] = not any(it.get("readout") == "label" for it in first)
    # B3: контракт запрещает выдумывать размер порции — вес вводит пользователь.
    m["b3_portion_null"] = all(it.get("portion_g") is None for it in first)

    comps = case.get("components") or []
    matched_idx: dict[int, dict] = {}
    used_positions: set[int] = set()
    for ci, comp in enumerate(comps):
        names = _component_names(comp)
        for pi, it in enumerate(first):
            if pi in used_positions:
                continue
            if _matches(it.get("name"), names):
                matched_idx[ci] = it
                used_positions.add(pi)
                break
    m["b4_recall"] = (len(matched_idx) / len(comps)) if comps else None
    # B5: фантом — позиция, не сопоставленная ни с одним размеченным компонентом.
    m["b5_has_phantom"] = len(used_positions) < len(first)

    sig = [_run_kcal_signature(r) for r in runs if r is not None]
    m["b6_cv"] = _cv(sig)
    name_sets = [{_norm(it.get("name")) for it in r} for r in runs if r is not None]
    if len(name_sets) >= 2:
        m["b7_jaccard"] = min(
            _jaccard(a, b)
            for i, a in enumerate(name_sets)
            for b in name_sets[i + 1:]
        )
    else:
        m["b7_jaccard"] = None

    # B8/B9 — только взвешенная пятёрка: без граммовок вычисленной истины нет.
    weighed = [c for c in comps if c.get("g") is not None and c.get("per100g")]
    if case.get("subset") == "B-w" and weighed:
        truth = {k: 0.0 for k in MACROS}
        model = {k: 0.0 for k in MACROS}
        for ci, comp in enumerate(comps):
            g = comp.get("g")
            ref = comp.get("per100g") or {}
            if g is None or not ref:
                continue
            for k in MACROS:
                if ref.get(k) is not None:
                    truth[k] += float(ref[k]) * float(g) / 100.0
            got = matched_idx.get(ci)
            if got is None:
                continue  # пропущенный компонент штрафуется через B8 автоматически
            gp = got.get("per100g") or {}
            for k in MACROS:
                if gp.get(k) is not None:
                    model[k] += float(gp[k]) * float(g) / 100.0
        m["b8_dev"] = (
            abs(model["kcal"] - truth["kcal"]) / truth["kcal"] if truth["kcal"] else None
        )
        m["b8_truth_kcal"] = truth["kcal"]
        m["b9"] = {
            who: (
                corr.get("kcal_lo") is not None
                and corr.get("kcal_hi") is not None
                and float(corr["kcal_lo"]) <= truth["kcal"] <= float(corr["kcal_hi"])
            )
            for who, corr in (case.get("corridor") or {}).items()
        }
    else:
        m["b8_dev"] = m["b8_truth_kcal"] = None
        m["b9"] = None
    return m


def c_expected_total(case: dict, a_index: dict) -> dict:
    """Истина набора C: Σ per100g_эталона(A) × граммовка / 100 (§3C.6)."""
    total = {k: 0.0 for k in MACROS}
    for ing in case.get("ingredients") or []:
        gt = a_index[ing["case"]]["ground_truth"]
        g = float(ing["g"])
        for k in MACROS:
            v = gt.get(k)
            if v is None:
                raise ValueError(
                    f"{case['id']}: у кейса {ing['case']} не заполнен макрос {k} — "
                    "эталон набора неопределён (§3D.5 п.5)"
                )
            total[k] += float(v) * g / 100.0
    return {k: round(v, 1) for k, v in total.items()}


def c_weight_mismatch(case: dict, threshold: float = C_WEIGHT_MISMATCH) -> bool:
    """C6: расходится ли заявленный вес блюда с Σ граммовок сверх порога."""
    total_g = sum(float(i["g"]) for i in (case.get("ingredients") or []))
    declared = case.get("declared_weight_g")
    if not total_g or declared is None:
        return False
    return abs(float(declared) - total_g) / total_g > threshold


def score_c_case(
    case: dict,
    a_index: dict,
    items: list[dict] | None,
    *,
    dish_positions: int | None = None,
) -> dict:
    """Метрики яруса C по одному набору (§3C.4).

    `items` — позиции, извлечённые моделью; у каждой ожидаются `name`, `g`
    (граммовка, присвоенная из текста состава) и `per100g`. Итог НЕ берётся у
    модели: по решению L2 его считает код — именно поэтому C4 с допуском 0.5%
    работает детектором бага агрегации, а не оценкой модели.
    """
    m: dict = {"c_json_valid": items is not None}
    if items is None:
        return m

    ings = case.get("ingredients") or []
    # Сопоставляем позиции с ингредиентами по имени эталонного кейса A.
    matched: dict[str, list[dict]] = {}
    used: set[int] = set()
    for ing in ings:
        gt = a_index[ing["case"]]["ground_truth"]
        names = [gt.get("name", "")]
        for pi, it in enumerate(items):
            if pi in used:
                continue
            if _matches(it.get("name"), names):
                matched.setdefault(ing["case"], []).append(it)
                used.add(pi)
    missing = [i["case"] for i in ings if i["case"] not in matched]
    phantom = [items[pi].get("name") for pi in range(len(items)) if pi not in used]
    m["c1_missing"] = missing
    m["c1_phantom"] = phantom
    m["c1_ok"] = not missing and not phantom
    m["c7_all_ingredients"] = not missing  # батчи §5.2: на границе ничего не потеряно

    # C2 — граммовка каждой позиции взята из текста состава именно для неё.
    bad_g = []
    for ing in ings:
        for it in matched.get(ing["case"], []):
            if it.get("g") is None or abs(float(it["g"]) - float(ing["g"])) > 1e-9:
                bad_g.append((ing["case"], it.get("g"), ing["g"]))
    m["c2_bad_grams"] = bad_g
    m["c2_ok"] = not bad_g

    # C3 — per100g позиции против эталона яруса A (те же этикетки).
    hits = misses = 0
    devs = []
    for ing in ings:
        gt = a_index[ing["case"]]["ground_truth"]
        for it in matched.get(ing["case"], []):
            gp = it.get("per100g") or {}
            worst = 0.0
            for k in MACROS:
                ref, got = gt.get(k), gp.get(k)
                if ref in (None, 0) or got is None:
                    continue
                worst = max(worst, abs(float(got) - float(ref)) / abs(float(ref)))
            devs.append(worst)
            if worst <= C_PER100G_TOL:
                hits += 1
            else:
                misses += 1
    m["c3_share"] = (hits / (hits + misses)) if (hits + misses) else None
    m["c3_max_dev"] = max(devs) if devs else None
    m["c9_mean_dev"] = (sum(devs) / len(devs)) if devs else None  # C-text, без порога

    # C4 — итог кода против вычисленной истины. Максимум по четырём макросам.
    truth = c_expected_total(case, a_index)
    got_total = {k: 0.0 for k in MACROS}
    for it in items:
        g = it.get("g")
        gp = it.get("per100g") or {}
        if g is None:
            continue
        for k in MACROS:
            if gp.get(k) is not None:
                got_total[k] += float(gp[k]) * float(g) / 100.0
    m["c4_dev"] = max(
        (abs(got_total[k] - truth[k]) / truth[k]) for k in MACROS if truth[k]
    )
    m["c4_ok"] = m["c4_dev"] <= C_TOTAL_TOL

    # C5 — dish-режим обязан свести всё в одну итоговую позицию.
    m["c5_ok"] = None if dish_positions is None else dish_positions == 1

    # C8 — повторный кадр того же товара даёт ОДНУ позицию (дедуп §5.5 через
    # границу батча). Известное ограничение §3C.2: дедуп нормализует только
    # регистр и пробелы, поэтому отчёт обязан различать «дедуп не сработал» и
    # «имена разошлись» — иначе метрика обвинит не тот механизм.
    dupes = {cid: v for cid, v in matched.items() if len(v) > 1}
    m["c8_ok"] = not dupes
    m["c8_names_normalized_equal"] = {
        cid: len({_norm(x.get("name")) for x in v}) == 1 for cid, v in dupes.items()
    }

    # C6 — свойство НАБОРА, а не ответа: положительный контроль (C-05) обязан
    # срабатывать, отрицательные (C-01…C-04) — молчать.
    m["c6_flag"] = c_weight_mismatch(case)
    m["c6_ok"] = m["c6_flag"] == bool(case.get("expect_weight_mismatch"))
    return m


# ── Прогон ────────────────────────────────────────────────────────────────────

async def run_case(extractor: LLMExtractor, img_path: Path, retries: int) -> dict:
    """Один вызов прод-пути. Возвращает сырьё: items/usage/latency/error."""
    # Сжимаем как прод-фронт (см. prep_image) → всегда JPEG.
    mime = "image/jpeg"
    images = [prep_image(img_path)]
    attempt = 0
    while True:
        t0 = time.monotonic()
        try:
            items = await extractor.extract(images=images, mime_type=mime)
            latency = time.monotonic() - t0
            return {
                "items": [it.model_dump() for it in items],
                "usage": extractor.last_usage,
                "raw": extractor.last_raw,
                "latency_s": round(latency, 2),
                "error": None,
            }
        except LLMRateLimitError as e:
            cause = getattr(e, "__cause__", None)
            detail = f"rate_limit: {cause}" if cause is not None else f"rate_limit: {e}"
            if attempt >= retries:
                return {"items": None, "usage": None, "raw": extractor.last_raw,
                        "latency_s": round(time.monotonic() - t0, 2),
                        "error": detail}
            wait = 10 * (2 ** attempt)
            print(f"    429 — жду {wait}s (попытка {attempt + 1}/{retries})")
            await asyncio.sleep(wait)
            attempt += 1
        except LLMError as e:
            # Прод-путь маскирует APIError в общий текст; настоящую причину
            # (код/тело) достаём из __cause__ — для eval это важнее вежливого текста.
            cause = getattr(e, "__cause__", None)
            detail = f"{type(e).__name__}: {e}"
            if cause is not None:
                detail += f" | cause: {getattr(cause, 'status_code', '')} {cause}"
            return {"items": None, "usage": None, "raw": extractor.last_raw,
                    "latency_s": round(time.monotonic() - t0, 2), "error": detail}
        except Exception as e:  # noqa: BLE001 — раннер не должен падать на одном кейсе
            return {"items": None, "usage": None, "raw": None,
                    "latency_s": round(time.monotonic() - t0, 2),
                    "error": f"unexpected {type(e).__name__}: {e}"}


async def run_model(provider: str, model: str, tier: str, repeat: int,
                    limit: int | None, sleep_s: float, retries: int,
                    reasoning: str | None = None) -> Path:
    prov = PROVIDERS[provider]
    key = prov.key()
    if not key:
        sys.exit(f"Нет ключа для {provider}: не задан settings.{prov.key_attr} (.env)")

    # --reasoning переопределяет дефолт провайдера (замер влияния рассуждения на
    # кДж-конфузию/точность). Тег добавляется в имя файла прогона, чтобы none/low
    # не перезаписывали друг друга.
    reasoning_effort = reasoning if reasoning is not None else prov.reasoning_effort
    # При включённом reasoning: (1) запас бюджета вывода — reasoning-токены счётаются
    # в тот же лимит, иначе JSON не долетает (как у nemotron-omni); (2) не слать
    # temperature — reasoning-модели OpenAI принимают только дефолт 1 (400 на 0.2).
    reasoning_on = reasoning_effort not in ("", "none")
    out_budget = 6000 if reasoning_on else None
    temperature = None if reasoning_on else 0.2
    target = LLMTarget(
        base_url=prov.base_url, api_key=key, model=model,
        reasoning_effort=reasoning_effort, max_images=prov.max_images,
        extra_headers=prov.extra_headers, max_tokens_param=prov.max_tokens_param,
        max_output_tokens=out_budget, temperature=temperature,
        send_response_format=prov.send_response_format,
    )
    extractor = LLMExtractor(target=target)

    cases = load_cases(tier)
    if limit:
        cases = cases[:limit]
    print(f"\n{'=' * 66}\n{provider} · {model} · ярус {tier} · {len(cases)} кейсов "
          f"× {repeat}\n{'=' * 66}")

    records = []
    for i, case in enumerate(cases, 1):
        img = EVAL_DIR / case["image"]
        if not img.exists():
            print(f"  [{i}/{len(cases)}] {case['id']}: НЕТ ФАЙЛА {img}")
            records.append({"id": case["id"], "trap": case.get("trap"),
                            "runs": [{"error": "missing image"}], "scores": []})
            continue
        runs, scores = [], []
        for r in range(repeat):
            res = await run_case(extractor, img, retries)
            first = res["items"][0] if res["items"] else None
            sc = score_case(case, first)
            runs.append(res)
            scores.append(sc)
            tag = "ok " if sc["json_valid"] else "ERR"
            extra = ""
            if sc["json_valid"]:
                kh = "K✓" if sc.get("kcal_hit") else ("K~" if sc.get("kcal_diag") else "K✗")
                extra = f"{kh} kcal_rel={sc.get('kcal_rel')}"
                if sc.get("kj_confusion"):
                    extra += " ⚠кДж-конфузия"
            else:
                extra = res["error"] or ""
            print(f"  [{i}/{len(cases)}] {case['id']:<6} {case.get('trap',''):<18} "
                  f"{tag} {extra}")
            if sleep_s:
                await asyncio.sleep(sleep_s)
        records.append({"id": case["id"], "trap": case.get("trap"),
                        "runs": runs, "scores": scores})

    RUNS_DIR.mkdir(exist_ok=True)
    slug = model.replace("/", "_").replace(":", "_")
    # Тег reasoning в имени, чтобы none/low-прогоны одной модели не затирали друг друга.
    if reasoning is not None:
        slug += f"_r-{reasoning or 'none'}"
    # Проверку целостности данных делаем ДО метрик: если фото и эталон разъехались,
    # любые проценты ниже бессмысленны, и это надо увидеть первым, а не в конце.
    alerts = check_desync(cases, records)

    out = RUNS_DIR / f"{date.today().isoformat()}_{slug}.json"
    out.write_text(json.dumps({
        "provider": provider, "model": model, "tier": tier, "repeat": repeat,
        "reasoning_effort": reasoning_effort,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "desync_alerts": alerts,
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСырьё → {out}")
    print_desync(alerts)
    _print_summary(provider, model, records)
    if alerts:
        print("⚠ Напоминание: выше есть подозрения на рассинхрон — цифры сводки "
              "считать предварительными.")
    return out


# ── Агрегация ────────────────────────────────────────────────────────────────

def _aggregate(records: list[dict]) -> dict:
    """Сводит записи (по первому прогону каждого кейса) в метрики §4."""
    flat = [(rec, rec["scores"][0]) for rec in records
            if rec.get("scores") and rec["scores"]]
    n = len(flat)
    valid = [s for _, s in flat if s.get("json_valid")]
    nv = len(valid)

    def rate(pred, pool):
        pool = list(pool)
        return (sum(1 for x in pool if pred(x)) / len(pool)) if pool else None

    bju_hits = sum(s.get("bju_hits", 0) for s in valid)
    bju_total = sum(s.get("bju_total", 0) for s in valid)
    lat = [rec["runs"][0].get("latency_s") for rec, _ in flat
           if rec["runs"] and rec["runs"][0].get("latency_s") is not None]
    tokens_in = sum((rec["runs"][0].get("usage") or {}).get("prompt_tokens", 0)
                    for rec, _ in flat if rec["runs"] and rec["runs"][0].get("usage"))
    tokens_out = sum((rec["runs"][0].get("usage") or {}).get("completion_tokens", 0)
                     for rec, _ in flat if rec["runs"] and rec["runs"][0].get("usage"))
    return {
        "n": n,
        "json_valid": rate(lambda s: s.get("json_valid"), (s for _, s in flat)),
        "kcal_hit": rate(lambda s: s.get("kcal_hit"), valid),
        "kcal_diag": rate(lambda s: s.get("kcal_diag"), valid),
        "bju_hit": (bju_hits / bju_total) if bju_total else None,
        "kj_confusion_n": sum(1 for s in valid if s.get("kj_confusion")),
        "base_confused_n": sum(1 for s in valid if s.get("base_confused")),
        "name_ok": rate(lambda s: s.get("name_ok"), valid),
        "brand_ok": rate(lambda s: s.get("brand_ok"),
                         (s for s in valid if s.get("brand_ok") is not None)),
        "lat_med": (sorted(lat)[len(lat) // 2] if lat else None),
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "nv": nv,
    }


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def _print_summary(provider, model, records):
    a = _aggregate(records)
    print(f"\n── Сводка {provider}/{model} ── (n={a['n']}, JSON-валидных {a['nv']})")
    print(f"  JSON-валидность : {_pct(a['json_valid'])}  (порог 100%)")
    print(f"  kcal-hit ≤2%    : {_pct(a['kcal_hit'])}  (порог ≥90%)  | ≤10%: {_pct(a['kcal_diag'])}")
    print(f"  БЖУ-hit ≤2%     : {_pct(a['bju_hit'])}  (порог ≥85%)")
    print(f"  кДж-конфузий    : {a['kj_confusion_n']}  (порог 0)")
    print(f"  база per100g    : путаниц {a['base_confused_n']}")
    print(f"  name/brand      : {_pct(a['name_ok'])} / {_pct(a['brand_ok'])}")
    print(f"  латентность med : {a['lat_med']}s  · токены {a['tokens_in']}/{a['tokens_out']}")


def build_report() -> Path:
    runs = sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
    runs = [p for p in runs if not p.name.endswith("_report.md")]
    if not runs:
        sys.exit("Нет прогонов в tests/eval/runs/ — сначала `run`.")

    rows, traps_all = [], {}
    for p in runs:
        d = json.loads(p.read_text(encoding="utf-8"))
        a = _aggregate(d["records"])
        model = d["model"]
        # Тег reasoning, чтобы none/low одной модели не сливались в одну строку.
        reff = d.get("reasoning_effort")
        if reff and reff != "none":
            model += f" (r={reff})"
        rows.append((d["provider"], model, a, d["records"]))

    lines = [f"# Eval ярус A — сводка ({date.today().isoformat()})", ""]
    lines.append("Пороги приёмки — llm-eval-dataset.md §4. Прочерк = метрика не считалась.")
    lines.append("")
    lines.append("| провайдер | модель | JSON | kcal≤2% | kcal≤10% | БЖУ≤2% | кДж-конф | name | brand | lat med | ток in/out | $/прогон |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for prov, model, a, _ in rows:
        tin, tout = a["tokens_in"], a["tokens_out"]
        if model in TARIFFS:
            pin, pout = TARIFFS[model]
            cost = f"${(tin * pin + tout * pout) / 1e6:.4f}"
        else:
            cost = "—"  # тариф неизвестен → цену считать по токенам
        lines.append(
            f"| {prov} | `{model}` | {_pct(a['json_valid'])} | {_pct(a['kcal_hit'])} | "
            f"{_pct(a['kcal_diag'])} | {_pct(a['bju_hit'])} | {a['kj_confusion_n']} | "
            f"{_pct(a['name_ok'])} | {_pct(a['brand_ok'])} | {a['lat_med']}s | "
            f"{tin}/{tout} | {cost} |"
        )
    lines += ["", "**Гейт §4:** JSON=100%, kcal≤2%≥90%, БЖУ≤2%≥85%, кДж-конфузий=0.",
              "Провал любого = модель не в прод.", ""]

    # Разбивка по ловушкам (важнее среднего: «90% в среднем, но 0% на кДж» = провал)
    lines.append("## По ловушкам (kcal-hit ≤2% / кДж-конфузии, первый прогон)")
    lines.append("")
    for prov, model, a, records in rows:
        by_trap: dict[str, list] = {}
        for rec in records:
            if not rec.get("scores"):
                continue
            s = rec["scores"][0]
            by_trap.setdefault(rec.get("trap") or "?", []).append(s)
        parts = []
        for trap, ss in sorted(by_trap.items()):
            valid = [x for x in ss if x.get("json_valid")]
            hit = sum(1 for x in valid if x.get("kcal_hit"))
            kj = sum(1 for x in valid if x.get("kj_confusion"))
            parts.append(f"{trap}: {hit}/{len(ss)}" + (f" ⚠кДж×{kj}" if kj else ""))
        lines.append(f"- **{model}** — " + "; ".join(parts))
    lines.append("")

    out = RUNS_DIR / f"{date.today().isoformat()}_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Отчёт → {out}")
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Прогон eval-датасета КБЖУ (ТЗ §6)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="прогнать одну модель")
    # Дефолт = прод-цель (ADR-0017 §16). Сходимость с config.py проверяет
    # tests/eval/test_manifest.py::test_eval_default_matches_prod_target.
    r.add_argument("--provider", default=DEFAULT_PROVIDER, choices=list(PROVIDERS))
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--tier", default="A")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--limit", type=int, default=None, help="только первые N кейсов")
    r.add_argument("--sleep", type=float, default=1.0, help="пауза между вызовами, с")
    r.add_argument("--retries", type=int, default=3, help="ретраев на 429")
    r.add_argument("--reasoning", default=None,
                   help="переопределить reasoning_effort (none/low/medium/high); "
                        "тег попадёт в имя файла прогона")

    sub.add_parser("report", help="свести все runs/*.json в markdown")

    args = ap.parse_args()
    if args.cmd == "report":
        build_report()
        return
    asyncio.run(run_model(args.provider, args.model, args.tier, args.repeat,
                          args.limit, args.sleep, args.retries, args.reasoning))


if __name__ == "__main__":
    main()
