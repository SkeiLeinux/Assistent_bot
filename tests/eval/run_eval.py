"""tests/eval/run_eval.py — прогон eval-датасета по кандидату (ТЗ §6). НЕ pytest.

Отдельный скрипт, а не тест: делает живые сетевые вызовы и логирует цену — в общем
`pytest`-прогоне ему не место. Гоняет ярус A `manifest.yaml` через ТОТ ЖЕ путь, что
прод (`LLMExtractor`), меняя лишь цель (провайдер/модель) — то есть раннер является
первым потребителем абстракции ADR-0017 §6 и способом проверить, что она годная,
до перевода прода.

Запуск (из корня репо):
    python -m tests.eval.run_eval run                       # дефолт = прод-цель, ярус A
    python -m tests.eval.run_eval run --provider openai     --model gpt-5.4
    python -m tests.eval.run_eval run --prompt v1           # прежний промпт, для сравнения
    python -m tests.eval.run_eval run --tier C --mode dish_combined
    python -m tests.eval.run_eval run --tier C --mode dish_text     # C9, без единого кадра
    python -m tests.eval.run_eval report      # свести все runs/*.json в markdown

Ярус C гоняется через ПРОД-агрегацию (`services.food_service.aggregate_dish`), а не
через свою копию расчёта: смысл метрики C4 (допуск 0.5%) — быть детектором бага
именно в коде агрегации, и своя копия этот смысл уничтожила бы.

`--prompt` существует ради требования приёмки ТЗ §12.3: промпт меняется, значит
«не хуже baseline» доказуемо только прогоном ОБЕИХ версий на одних и тех же кейсах.

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
from app.contexts.nutrition.resolve import prompts  # noqa: E402
from app.contexts.nutrition.resolve.checks import FLAG_WEIGHT_MISMATCH  # noqa: E402
from app.contexts.nutrition.resolve.extractor import LLMExtractor  # noqa: E402
from app.contexts.nutrition.services.food_service import aggregate_dish  # noqa: E402
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
        # Список кандидатов, а не одно имя. Причина — у ПРОД-провайдера ключ
        # называется `llm_api_key`, потому что шов проходит по OpenAI-совместимому
        # маршруту, а не по вендору (ADR-0017 §16), и заводить в `.env` второй
        # ключ с тем же значением ради раннера незачем. `openai_api_key` остаётся
        # первым по приоритету: если его задали ОТДЕЛЬНО, это сделали намеренно
        # (другая квота/биллинг под замер), и молча его игнорировать нельзя.
        self.key_attrs = (key_attr,) if isinstance(key_attr, str) else tuple(key_attr)
        self.reasoning_effort = reasoning_effort
        self.max_images = max_images
        self.extra_headers = extra_headers or {}
        self.max_tokens_param = max_tokens_param
        self.send_response_format = send_response_format

    def key(self):
        # Ключи в конфиге — SecretStr (чтобы не утекали в трейсбеки, см.
        # app/config.py). Раннеру нужно открытое значение: оно уходит в SDK.
        for attr in self.key_attrs:
            secret = getattr(settings, attr, None)
            raw = secret.get_secret_value() if secret is not None else None
            if raw:
                return raw
        return None

    @property
    def key_hint(self) -> str:
        """Что писать в ошибке: перечислить ВСЕ принимаемые имена, а не первое.

        Сообщение «не задан settings.openai_api_key» отправляло чинить `.env`
        там, где чинить нечего: прод-ключ уже лежал в `LLM_API_KEY`.
        """
        return " или ".join(a.upper() for a in self.key_attrs)


# Таймаут ОДНОГО вызова в замере. Намеренно много: раннер измеряет латентность,
# а не обеспечивает UX, и обрезать медленную модель по прод-таймауту значит
# записать ей отказ вместо честного «21.9 с» (ровно столько давал qwen3.6-27b).
EVAL_TIMEOUT_S = 180.0

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
        # Ключ — тоже параметр прод-цели, и его в этой сверке не хватало:
        # прод ходит по `LLM_API_KEY`, а раннер требовал `OPENAI_API_KEY`, то есть
        # на машине с корректным прод-`.env` замер не запускался вовсе.
        # `OPENAI_API_KEY` остаётся ПЕРВЫМ: заданный отдельно, он задан намеренно.
        "https://api.openai.com/v1", ("openai_api_key", "llm_api_key"),
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

def load_all() -> list[dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return [c for c in data if isinstance(c, dict) and "id" in c]


def a_index() -> dict:
    """Все записи яруса A по id — истина ингредиентов набора C (§3C.6)."""
    return {c["id"]: c for c in load_all() if c.get("tier") == "A"}


def load_cases(tier: str | None) -> list[dict]:
    cases = load_all()
    if tier:
        cases = [c for c in cases if c.get("tier") == tier]
    if tier == "C":
        # У наборов C своего кадра нет: фотографии — ссылки на файлы яруса A
        # (§3C.1). Правила исключения ниже к ним неприменимы по построению.
        return cases
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


def load_refusal_cases(tier: str | None) -> list[dict]:
    """Кейсы под метрику «отказ вместо выдумки» (ТЗ §12.3, ADR-0017 §17).

    Это `status: unscorable` — снимок есть, но эталон из него НЕ выводится, потому
    что этикетка физически нечитаема (A-07: обёртка перекручена, числа продиктованы
    извне). Мерить на таком точность чтения бессмысленно — это тест человека, а не
    модели, поэтому из основного зачёта кейс исключён. Но ровно он и есть материал
    для новой метрики: правильный ответ здесь — `readout ∈ {estimate, unreadable}`,
    то есть признание, а не правдоподобные числа. Все три финалиста замера
    2026-07-27 промахнулись именно тут, объявив прочитанным то, что не читается.
    """
    return [
        c for c in load_all()
        if (tier is None or c.get("tier") == tier)
        and c.get("status") == "unscorable"
        and c.get("image")
    ]


def score_refusal(items: list[dict] | None) -> dict:
    """Метрика отказа: модель обязана НЕ заявлять «label» на нечитаемой этикетке."""
    if not items:
        return {"json_valid": False, "refusal_ok": False, "readout": None}
    readout = items[0].get("readout")
    return {
        "json_valid": True,
        "readout": readout,
        "note": items[0].get("note"),
        "refusal_ok": readout in ("estimate", "unreadable"),
    }


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
    """C6: расходится ли Σ граммовок с заявленным весом блюда сверх порога.

    Формула — дословно из ТЗ контракта §6.4:
        |Σ portion_g_i − weight_g| / weight_g > 0.30
    Знаменатель — **заявленный вес**, а не Σ граммовок. Разница не косметическая:
    на C-05 (Σ 500 г, заявлено 350 г) Σ-знаменатель даёт ровно 30.0% и НЕ проходит
    строгое `>`, то есть положительный контроль C6+ молча не сработал бы. Поймано
    пересчётом процентов из §3C.2 при сборке яруса C, а не тестом — тесты
    проходили при обеих формулах, потому что синтетика была далеко от границы.
    """
    total_g = sum(float(i["g"]) for i in (case.get("ingredients") or []))
    declared = case.get("declared_weight_g")
    if not declared:
        return False
    return abs(total_g - float(declared)) / float(declared) > threshold


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


# ── Прогон яруса C: сборка ввода из «бумажного» набора (§3C.1) ───────────────

def c_image_paths(case: dict, index: dict) -> list[Path]:
    """Кадры набора: этикетки ингредиентов + повторные ракурсы, В ЭТОМ порядке.

    Порядок обязателен и не перемешивается: C-04 рассчитан на то, что оригиналы
    попадут в ПЕРВЫЙ батч, а дубли тех же товаров — во второй, и только так дедуп
    (§5.5 ТЗ) проверяется через границу батча, а не внутри одного вызова.
    """
    paths = []
    for ing in case.get("ingredients") or []:
        image = index[ing["case"]].get("image")
        if image:
            paths.append(EVAL_DIR / image)
    for extra in case.get("extra_images") or []:
        paths.append(EVAL_DIR / extra)
    return paths


def c_composition_text(case: dict) -> str:
    """Состав словами: «макароны сухие 100 г, паста томатная 50 г».

    Берём `text_name`, а не имя с этикетки: «Спиральки-экспресс» не сообщают, что
    макароны СУХИЕ, и модель по умолчанию отдаст per100g варёных (~130 против 342)
    — метрика C9 записала бы верное поведение как промах втрое (§3C.2 п.3).
    """
    return ", ".join(
        f"{ing['text_name']} {ing['g']} г" for ing in case.get("ingredients") or []
    )


def c_items_view(extracted: list) -> list[dict]:
    """ExtractedItem → форма, которую ждут метрики C (`name`/`g`/`per100g`)."""
    out = []
    for it in extracted:
        per = it.per100g
        out.append({
            "name": it.name,
            "g": it.portion_g,
            "readout": it.readout,
            "per100g": None if per is None else {
                "kcal": per.kcal, "protein": per.protein,
                "fat": per.fat, "carbs": per.carbs,
            },
            "warnings": list(it.warnings),
        })
    return out


async def run_c_case(
    extractor: LLMExtractor, case: dict, index: dict, mode: str, retries: int
) -> dict:
    """Один набор C через прод-путь: извлечение + ПРОД-агрегация."""
    images = []
    if mode in (prompts.MODE_DISH_PHOTOS, prompts.MODE_DISH_COMBINED):
        images = [prep_image(p) for p in c_image_paths(case, index)]
    text = None
    if mode in (prompts.MODE_DISH_TEXT, prompts.MODE_DISH_COMBINED):
        text = c_composition_text(case)

    attempt = 0
    while True:
        t0 = time.monotonic()
        try:
            extracted = await extractor.extract(
                mode=mode, text=text, images=images or None,
                dish_name=case["dish_name"], weight_g=case["declared_weight_g"],
            )
            agg = aggregate_dish(
                extracted,
                name=case["dish_name"],
                weight_g=case["declared_weight_g"],
                single=(mode == prompts.MODE_DISH_PHOTOS),
            )
            return {
                "items": c_items_view(extracted),
                "total": agg.item.model_dump(),
                # Чистый гейт КОДА: итог прод-агрегации против независимого
                # пересчёта из ТЕХ ЖЕ позиций. Нужен отдельно от C4, потому что
                # C4 сравнивает с эталоном и потому смешивает две разные вещи —
                # ошибку арифметики кода и ошибку per100g модели (последняя по
                # C3 допустима до 2%, то есть C4 с допуском 0.5% не может быть
                # «детектором бага в коде», как заявлено в §3C.4 датасета).
                "agg_self_dev": _agg_self_dev(extracted, agg, mode, case),
                "dish_positions": len(agg.positions),
                # C6 глазами КОДА: флаг, который реально подняла проверка на
                # граммовках из ответа модели. Отличается от свойства набора,
                # если модель перепутала граммовки — и это честный сигнал.
                "weight_mismatch": FLAG_WEIGHT_MISMATCH in agg.item.warnings,
                "calls": extractor.last_call_count,
                "usage": extractor.last_usage,
                "raw": extractor.last_raw,
                "latency_s": round(time.monotonic() - t0, 2),
                "error": None,
            }
        except LLMRateLimitError as e:
            if attempt >= retries:
                return _c_failure(extractor, t0, f"rate_limit: {e}")
            wait = 10 * (2 ** attempt)
            print(f"    429 — жду {wait}s (попытка {attempt + 1}/{retries})")
            await asyncio.sleep(wait)
            attempt += 1
        except LLMError as e:
            cause = getattr(e, "__cause__", None)
            detail = f"{type(e).__name__}: {e}"
            if cause is not None:
                detail += f" | cause: {getattr(cause, 'status_code', '')} {cause}"
            return _c_failure(extractor, t0, detail)
        except Exception as e:  # noqa: BLE001 — раннер не падает на одном наборе
            return _c_failure(extractor, t0, f"unexpected {type(e).__name__}: {e}")


def _agg_self_dev(extracted, agg, mode: str, case: dict) -> float | None:
    """Насколько прод-агрегация расходится с независимым пересчётом ТЕХ ЖЕ позиций.

    Это и есть детектор бага в коде агрегации в чистом виде: эталон здесь не
    участвует вовсе, поэтому ошибки модели в него не протекают. Ноль ожидается
    всегда, кроме округления последнего знака.
    """
    single = mode == prompts.MODE_DISH_PHOTOS
    weight = float(case["declared_weight_g"])
    expected = {k: 0.0 for k in MACROS}
    for it in extracted:
        if it.per100g is None:
            continue
        factor = weight / 100.0 if single else (
            (it.portion_g / 100.0) if it.portion_g else None
        )
        if factor is None:
            continue
        for k in MACROS:
            expected[k] += getattr(it.per100g, k) * factor
    got = agg.item.model_dump()
    devs = [
        abs(got[k] - expected[k]) / expected[k]
        for k in MACROS if expected[k]
    ]
    return max(devs) if devs else None


def _c_failure(extractor: LLMExtractor, t0: float, error: str) -> dict:
    return {
        "items": None, "total": None, "agg_self_dev": None, "dish_positions": None,
        "weight_mismatch": None, "calls": extractor.last_call_count,
        "usage": extractor.last_usage, "raw": extractor.last_raw,
        "latency_s": round(time.monotonic() - t0, 2), "error": error,
    }


def print_c_summary(records: list[dict]) -> None:
    """Сводка яруса C. Гейт модели и гейт кода печатаются РАЗДЕЛЬНО (§4 датасета).

    C4 и C6 не различают ни моделей, ни версий промпта — они про арифметику и
    про порог в коде. Смешать их со средним по модели значило бы получить число,
    которое ни на один вопрос не отвечает.
    """
    print("\n── Сводка ярус C ──")
    print("  набор  C1   C2   C3        C4(эталон) агрег.  C5  C6(код)  C7  C8  вызовов")
    for rec in records:
        s = rec.get("scores") or {}
        if not s:
            print(f"  {rec['id']:<6} — прогон не состоялся: {rec.get('error')}")
            continue

        def m(flag):
            return "—" if flag is None else ("✓" if flag else "✗")

        c3 = "—" if s.get("c3_share") is None else f"{s['c3_share'] * 100:.0f}%"
        c4 = "—" if s.get("c4_dev") is None else f"{s['c4_dev'] * 100:.2f}%"
        sd = s.get("agg_self_dev")
        agg = "—" if sd is None else f"{sd * 100:.3f}%"
        print(
            f"  {rec['id']:<6} {m(s.get('c1_ok'))}    {m(s.get('c2_ok'))}    "
            f"{c3:<9} {c4:<10} {agg:<7} {m(s.get('c5_ok'))}   "
            f"{m(s.get('c6_code_ok'))}       "
            f"{m(s.get('c7_all_ingredients'))}   {m(s.get('c8_ok'))}   {rec.get('calls')}"
        )
    print("\n  C1/C2/C3/C5/C7/C8 — гейт МОДЕЛИ; C6 — гейт КОДА.")
    print("  «агрег.» — прод-агрегация против независимого пересчёта ТЕХ ЖЕ позиций:")
    print("  чистый гейт кода, эталон в него не входит. Ожидается ~0.")
    print("  C4 сравнивает с ЭТАЛОНОМ и потому смешивает ошибку кода с ошибкой")
    print("  per100g модели (по C3 допустимой до 2%) — см. отчёт волны 1.")
    print("  C9 (режим dish_text) — калибруется, порога нет: c9_mean_dev по кейсам.")


async def run_tier_c(extractor: LLMExtractor, mode: str, limit, sleep_s, retries):
    index = a_index()
    cases = load_cases("C")
    if limit:
        cases = cases[:limit]
    records = []
    for i, case in enumerate(cases, 1):
        res = await run_c_case(extractor, case, index, mode, retries)
        scores = score_c_case(
            case, index, res["items"], dish_positions=res["dish_positions"]
        )
        if res["items"] is not None:
            scores["agg_self_dev"] = res["agg_self_dev"]
            scores["c6_code_flag"] = res["weight_mismatch"]
            scores["c6_code_ok"] = res["weight_mismatch"] == bool(
                case.get("expect_weight_mismatch")
            )
        tag = "ok " if res["items"] is not None else "ERR"
        print(
            f"  [{i}/{len(cases)}] {case['id']:<6} {tag} "
            f"позиций={len(res['items'] or [])} вызовов={res['calls']} "
            f"{res['error'] or ''}"
        )
        records.append({
            "id": case["id"], "mode": mode, "calls": res["calls"],
            "run": res, "scores": scores, "error": res["error"],
        })
        if sleep_s:
            await asyncio.sleep(sleep_s)
    return records


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
                    reasoning: str | None = None,
                    prompt_version: str = prompts.DEFAULT_PROMPT_VERSION,
                    mode: str = prompts.MODE_DIARY) -> Path:
    prov = PROVIDERS[provider]
    key = prov.key()
    if not key:
        sys.exit(f"Нет ключа для {provider}: задай {prov.key_hint} в .env")

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
        # ⬇ Два параметра, где раннер ОБЯЗАН расходиться с продом (блок 11).
        # Таймаут: прод-значение 20 с выбрано под медиану победителя 1.3 с, а
        # раннер меряет и заведомо медленные модели — у `qwen/qwen3.6-27b`
        # замерено 21.9 с на вызов, то есть прод-таймаут превратил бы её замер
        # в сплошные ошибки и «доказал» бы негодность моделью, а не данными.
        # 180 с — не «без таймаута»: висящий сокет всё равно рвётся.
        timeout_s=EVAL_TIMEOUT_S,
        # Ретраи 429: у раннера СВОЙ backoff (10·2^n, флаг --retries) и своя
        # метрика латентности. Ретраи внутри клиента спали бы ВНУТРИ
        # `extract()`, то есть попадали бы в замеряемое время и завышали
        # медианную задержку — метрику, по которой принимается инкремент.
        retry_429=0,
    )
    extractor = LLMExtractor(target=target, prompt_version=prompt_version)

    if tier == "C":
        print(f"\n{'=' * 66}\n{provider} · {model} · ярус C · режим {mode} · "
              f"промпт {prompt_version}\n{'=' * 66}")
        records = await run_tier_c(extractor, mode, limit, sleep_s, retries)
        out = _write_run(
            provider, model, tier, repeat, reasoning_effort, prompt_version,
            records, alerts=[], mode=mode,
        )
        print_c_summary(records)
        return out

    cases = load_cases(tier)
    if limit:
        cases = cases[:limit]
    print(f"\n{'=' * 66}\n{provider} · {model} · ярус {tier} · промпт "
          f"{prompt_version} · {len(cases)} кейсов × {repeat}\n{'=' * 66}")

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

    # Метрика «отказ вместо выдумки» (ТЗ §12.3): кейсы, исключённые из зачёта по
    # числам, возвращаются в оценку по признанию нечитаемости. Считается отдельно
    # и в знаменатель точности НЕ входит — иначе одно смешалось бы с другим.
    refusal = []
    for case in load_refusal_cases(tier):
        img = EVAL_DIR / case["image"]
        if not img.exists():
            continue
        res = await run_case(extractor, img, retries)
        sc = score_refusal(res["items"])
        refusal.append({"id": case["id"], "run": res, "scores": sc})
        print(f"  [отказ] {case['id']:<6} readout={sc.get('readout')} "
              f"{'✓' if sc['refusal_ok'] else '✗ выдумал числа'}")
        if sleep_s:
            await asyncio.sleep(sleep_s)

    # Проверку целостности данных делаем ДО метрик: если фото и эталон разъехались,
    # любые проценты ниже бессмысленны, и это надо увидеть первым, а не в конце.
    alerts = check_desync(cases, records)

    out = _write_run(provider, model, tier, repeat, reasoning_effort,
                     prompt_version, records, alerts, refusal=refusal)
    print_desync(alerts)
    _print_summary(provider, model, records)
    if refusal:
        ok = sum(1 for r in refusal if r["scores"]["refusal_ok"])
        print(f"  отказ вместо выдумки: {ok}/{len(refusal)}  (порог: все)")
    if alerts:
        print("⚠ Напоминание: выше есть подозрения на рассинхрон — цифры сводки "
              "считать предварительными.")
    return out


def _write_run(provider, model, tier, repeat, reasoning_effort, prompt_version,
               records, alerts, *, refusal=None, mode=None) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    slug = model.replace("/", "_").replace(":", "_")
    # Теги в имени файла, чтобы прогоны одной модели не затирали друг друга:
    # версия промпта — ровно то, что сравнивается на приёмке.
    if prompt_version != prompts.DEFAULT_PROMPT_VERSION:
        slug += f"_p-{prompt_version}"
    if reasoning_effort:
        slug += f"_r-{reasoning_effort}"
    if tier != "A":
        slug += f"_tier{tier}"
    if mode and mode != prompts.MODE_DIARY:
        slug += f"_{mode}"
    out = RUNS_DIR / f"{date.today().isoformat()}_{slug}.json"
    out.write_text(json.dumps({
        "provider": provider, "model": model, "tier": tier, "repeat": repeat,
        "reasoning_effort": reasoning_effort, "prompt_version": prompt_version,
        "mode": mode,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "desync_alerts": alerts,
        "refusal": refusal or [],
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСырьё → {out}")
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


# Поля, без которых `build_report` разбирать файл не может. Держим списком
# рядом с чтением: каждый `d["..."]` ниже обязан быть здесь, иначе посторонний
# файл в runs/ снова уронит сводку — уже дважды (ключи `records`, `provider`).
_RUN_REQUIRED_KEYS = frozenset({"provider", "model", "records"})


def build_report() -> Path:
    runs = sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
    runs = [p for p in runs if not p.name.endswith("_report.md")]
    if not runs:
        sys.exit("Нет прогонов в tests/eval/runs/ — сначала `run`.")

    rows, c_rows = [], []
    for p in runs:
        d = json.loads(p.read_text(encoding="utf-8"))
        # В runs/ лежат не только прогоны раннера: замеры источника OFF
        # сохраняют туда же свои артефакты, а форма у них другая. Раньше
        # `report` падал на них `KeyError` — то есть ОДИН посторонний файл делал
        # недоступной сводку по ВСЕМ прогонам.
        #
        # Проверяем ВЕСЬ набор обязательных ключей, а не какой-то один. Первая
        # версия этой проверки смотрела только `records` и упала на следующем же
        # запуске: `check_off_value.py` пишет сюда `*_memory-tierA.json`, у
        # которого `records` ЕСТЬ, а `provider` нет. Проверка по одному ключу —
        # это проверка по симптому конкретного файла, а не по форме.
        #
        # Пропуск ЯВНЫЙ, с именем файла: прогон, у которого пропали поля, —
        # испорченный прогон, и молчание о нём означало бы, что модель выпала
        # из сводки незаметно.
        missing = _RUN_REQUIRED_KEYS - set(d)
        if missing:
            print(f"  пропущен (не прогон раннера, нет {sorted(missing)}): {p.name}")
            continue
        if d.get("tier") == "C":
            # У яруса C своя форма записей и свой гейт — в таблицу A он не ложится.
            c_rows.append(d)
            continue
        a = _aggregate(d["records"])
        model = d["model"]
        # Теги, чтобы прогоны одной модели не сливались в одну строку. Версия
        # промпта — главный из них: ради этого сравнения флаг и заводился.
        reff = d.get("reasoning_effort")
        if reff and reff != "none":
            model += f" (r={reff})"
        pv = d.get("prompt_version")
        if pv:
            model += f" [{pv}]"
        rows.append((d["provider"], model, a, d["records"], d.get("refusal") or []))

    # Все файлы отсеялись — писать пустую таблицу нельзя: пустой отчёт читается
    # как «прогонов нет, метрик нет», а на деле означает «ни один файл не похож
    # на прогон». Это разные диагнозы, и второй требует посмотреть в runs/.
    if not rows and not c_rows:
        sys.exit("В tests/eval/runs/ нет ни одного прогона раннера (см. пропуски выше).")

    lines = [f"# Eval ярус A — сводка ({date.today().isoformat()})", ""]
    lines.append("Пороги приёмки — llm-eval-dataset.md §4. Прочерк = метрика не считалась.")
    lines.append("")
    lines.append("| провайдер | модель | JSON | kcal≤2% | kcal≤10% | БЖУ≤2% | кДж-конф | отказ | name | brand | lat med | ток in/out | $/прогон |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for prov, model, a, _, refusal in rows:
        tin, tout = a["tokens_in"], a["tokens_out"]
        if model in TARIFFS:
            pin, pout = TARIFFS[model]
            cost = f"${(tin * pin + tout * pout) / 1e6:.4f}"
        else:
            cost = "—"  # тариф неизвестен → цену считать по токенам
        ref = (
            f"{sum(1 for r in refusal if r['scores']['refusal_ok'])}/{len(refusal)}"
            if refusal else "—"
        )
        lines.append(
            f"| {prov} | `{model}` | {_pct(a['json_valid'])} | {_pct(a['kcal_hit'])} | "
            f"{_pct(a['kcal_diag'])} | {_pct(a['bju_hit'])} | {a['kj_confusion_n']} | "
            f"{ref} | {_pct(a['name_ok'])} | {_pct(a['brand_ok'])} | {a['lat_med']}s | "
            f"{tin}/{tout} | {cost} |"
        )
    lines += ["", "**Гейт §4:** JSON=100%, kcal≤2%≥90%, БЖУ≤2%≥85%, кДж-конфузий=0.",
              "Провал любого = модель не в прод.",
              "«отказ» — метрика «отказ вместо выдумки» на `status: unscorable` "
              "(ТЗ §12.3): `readout ∈ {estimate, unreadable}` на физически "
              "нечитаемой этикетке. В знаменатель точности НЕ входит.", ""]

    # Разбивка по ловушкам (важнее среднего: «90% в среднем, но 0% на кДж» = провал)
    lines.append("## По ловушкам (kcal-hit ≤2% / кДж-конфузии, первый прогон)")
    lines.append("")
    for prov, model, a, records, _ in rows:
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

    # Ярус C — отдельной таблицей: его гейт делится на модельный (C1–C3, C5, C7, C8)
    # и кодовый (C4, C6), и смешивать их со средним по ярусу A нельзя.
    if c_rows:
        lines += ["## Ярус C (составное блюдо)", "",
                  "| прогон | набор | C1 | C2 | C3 | C4 (код) | C5 | C6 (код) | C7 | C8 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for d in c_rows:
            tag = f"`{d['model']}` [{d.get('prompt_version')}] {d.get('mode')}"
            for rec in d["records"]:
                s = rec.get("scores") or {}

                def m(v):
                    return "—" if v is None else ("✓" if v else "✗")

                c3 = "—" if s.get("c3_share") is None else f"{s['c3_share'] * 100:.0f}%"
                c4 = "—" if s.get("c4_dev") is None else f"{s['c4_dev'] * 100:.2f}%"
                lines.append(
                    f"| {tag} | {rec['id']} | {m(s.get('c1_ok'))} | {m(s.get('c2_ok'))} | "
                    f"{c3} | {c4} | {m(s.get('c5_ok'))} | {m(s.get('c6_code_ok'))} | "
                    f"{m(s.get('c7_all_ingredients'))} | {m(s.get('c8_ok'))} |"
                )
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
    r.add_argument("--prompt", default=prompts.DEFAULT_PROMPT_VERSION,
                   choices=list(prompts.SYSTEM_PROMPTS),
                   help="версия системного промпта; приёмка требует обе (ТЗ §12.3)")
    r.add_argument("--mode", default=prompts.MODE_DIARY, choices=list(prompts.MODES),
                   help="режим разбора; ярус C гоняется dish_* режимами")

    sub.add_parser("report", help="свести все runs/*.json в markdown")

    args = ap.parse_args()
    if args.cmd == "report":
        build_report()
        return
    asyncio.run(run_model(args.provider, args.model, args.tier, args.repeat,
                          args.limit, args.sleep, args.retries, args.reasoning,
                          args.prompt, args.mode))


if __name__ == "__main__":
    main()
