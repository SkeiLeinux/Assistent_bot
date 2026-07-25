#!/usr/bin/env python3
"""Проверка достижимости LLM-провайдеров с этой машины (ADR-0017 §12).

Зачем отдельный скрипт, а не curl-однострочник: у отказа три разных причины,
и по HTTP-коду они НЕ различаются, а действия по ним противоположные.

  1. СЕТЬ      — DNS/TCP/TLS не проходит. Виноват хостер/файрвол, провайдер ни при чём.
  2. ГЕО       — эндпоинт отвечает, но отказывает по локации. Провайдер закрыт, точка.
  3. КЛЮЧ      — эндпоинт отвечает, локация принята, ключ невалиден. Провайдер ДОСТУПЕН.

Ключевые грабли, из-за которых наивная проверка врёт:

  • **Список моделей — не показатель.** Gemini отдаёт гео-отказ (400 FAILED_PRECONDITION
    "User location is not supported for the API use") на вызове generateContent;
    GET /v1beta/models при этом может пройти. Проверять надо генерацию.
  • **Без ключа гео не проверить.** Google на запрос без ключа отвечает 403
    "API key not valid" — тем же кодом, что и часть гео-отказов. Нужен реальный ключ.
  • **Гео определяется не страной VPS, а его IP.** Известны отказы на хостерах
    в формально поддерживаемых странах (Германия) — блокируются диапазоны ДЦ.
    Вывод: «VPS вне РФ» ничего не доказывает, проверять надо с этой машины.
  • **GOOGLE_CLOUD_PROJECT в окружении** заставляет google-клиенты уходить на
    Vertex AI вместо Gemini API и давать ложный гео-отказ. Скрипт про это предупредит.

Groq здесь — контрольная группа: он у нас работает. Если Groq тоже упал в СЕТЬ,
проблема в машине, а не в провайдерах, и остальные вердикты недостоверны.

Зависимости: только стандартная библиотека (venv трогать не нужно).

Запуск:
    export GEMINI_API_KEY=...  OPENAI_API_KEY=...  ANTHROPIC_API_KEY=...  GROQ_API_KEY=...
    python3 scripts/check_llm_providers.py
    python3 scripts/check_llm_providers.py --only gemini --verbose
    python3 scripts/check_llm_providers.py --ipv4     # если у хостера гео v6 ≠ гео v4

Код возврата: 0 — все проверенные провайдера доступны; 1 — есть недоступные.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TIMEOUT = 15

# User-Agent обязателен. Groq (и не только) стоит за Cloudflare, а Cloudflare
# банит дефолтный `Python-urllib/3.x` как скраперную подпись и отдаёт
# `403 error code: 1010` — что выглядит как отказ провайдера, хотя провайдер
# запроса даже не увидел. Проверено на живом VPS 2026-07-25: с urllib-дефолтом
# Groq давал 1010, при том что прод-код через httpx работает.
USER_AGENT = "lifeManager-bot-provider-check/1.0"

# ── Вердикты ────────────────────────────────────────────────────────────────

OK = "ДОСТУПЕН"
GEO = "ЗАКРЫТ ПО ГЕО"
KEY = "ДОСТУПЕН (ключ невалиден)"
NET = "СЕТЬ НЕ ПРОХОДИТ"
NOKEY = "НЕ ПРОВЕРЕН (нет ключа)"
CF = "БЛОК CLOUDFLARE (не вендор)"
UNKNOWN = "НЕЯСНО — смотри тело ответа"

# Подстроки гео-отказа. Список эвристический и может отстать от формулировок
# вендоров — поэтому скрипт ВСЕГДА печатает тело ответа при неуспехе.
# Тело — истина, вердикт — подсказка.
GEO_MARKERS = (
    "user location is not supported",
    "location is not supported",
    "unsupported_country",
    "country, region, or territory",
    "not available in your region",
    "not available in your country",
    "unsupported region",
    "region not supported",
)

# Подстроки «ключ плохой» — означают, что до провайдера мы ДОЕХАЛИ.
KEY_MARKERS = (
    "api key not valid",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "authentication_error",
    "invalid x-api-key",
    "no auth credentials",
    "missing credentials",
    "unauthorized",
)

# Ошибки, которые доказывают доступность: мы прошли auth и гео, а споткнулись
# о конкретную модель или лимит. Для нашей задачи это успех.
REACHABLE_MARKERS = (
    "model_not_found",
    "does not exist",
    "not found for api version",
    "rate limit",
    "rate_limit",
    "quota",
    "insufficient_quota",
    "credit balance",
)


@dataclass
class Provider:
    name: str
    host: str
    env: tuple[str, ...]
    gen_url: str
    gen_body: dict
    # Как приклеить ключ: возвращает dict заголовков.
    auth: object
    list_url: str | None = None
    extra_headers: dict = field(default_factory=dict)


PROVIDERS = [
    Provider(
        name="gemini",
        host="generativelanguage.googleapis.com",
        env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        list_url="https://generativelanguage.googleapis.com/v1beta/models",
        # Модель подставляется динамически из списка; это дефолт на случай,
        # если список недоступен.
        gen_url=(
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-flash-latest:generateContent"
        ),
        gen_body={
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        },
        # Ключ заголовком, а не в URL: иначе он утечёт в логи и в вывод скрипта.
        auth=lambda k: {"x-goog-api-key": k},
    ),
    Provider(
        name="openai",
        host="api.openai.com",
        env=("OPENAI_API_KEY",),
        list_url="https://api.openai.com/v1/models",
        gen_url="https://api.openai.com/v1/chat/completions",
        gen_body={
            "model": "gpt-5.4-nano",
            "messages": [{"role": "user", "content": "ping"}],
            "max_completion_tokens": 1,
        },
        auth=lambda k: {"Authorization": f"Bearer {k}"},
    ),
    Provider(
        name="anthropic",
        host="api.anthropic.com",
        env=("ANTHROPIC_API_KEY",),
        list_url="https://api.anthropic.com/v1/models",
        gen_url="https://api.anthropic.com/v1/messages",
        gen_body={
            "model": "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        },
        auth=lambda k: {"x-api-key": k},
        extra_headers={"anthropic-version": "2023-06-01"},
    ),
    Provider(
        name="groq",  # контрольная группа: работает сейчас
        host="api.groq.com",
        env=("GROQ_API_KEY",),
        list_url="https://api.groq.com/openai/v1/models",
        gen_url="https://api.groq.com/openai/v1/chat/completions",
        gen_body={
            "model": "qwen/qwen3.6-27b",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        },
        auth=lambda k: {"Authorization": f"Bearer {k}"},
    ),
]


# ── Слой 1: сеть ─────────────────────────────────────────────────────────────

def check_network(host: str, force_ipv4: bool) -> tuple[bool, str]:
    """DNS → TCP:443 → TLS handshake. Отделяет проблемы машины от отказов вендора."""
    family = socket.AF_INET if force_ipv4 else 0
    try:
        infos = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, f"DNS не разрешается: {e}"
    if not infos:
        return False, "DNS вернул пустой результат"

    addrs = sorted({i[4][0] for i in infos})
    af, socktype, proto, _, sockaddr = infos[0]
    try:
        with socket.socket(af, socktype, proto) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect(sockaddr)
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                tls_ver = tls.version()
    except (OSError, ssl.SSLError) as e:
        return False, f"TCP/TLS до {sockaddr[0]}:443 не поднялся: {e}"
    return True, f"IP {', '.join(addrs)} · TLS {tls_ver}"


# ── Слой 2: HTTP ─────────────────────────────────────────────────────────────

def http(url: str, headers: dict, body: dict | None = None) -> tuple[int, str]:
    """Возвращает (код, тело). HTTPError — это тоже ответ, а не сбой."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return -1, f"URLError: {e.reason}"
    except (TimeoutError, socket.timeout):
        return -1, "таймаут"


def classify(code: int, body: str) -> str:
    low = body.lower()
    if code == -1:
        return NET
    if 200 <= code < 300:
        return OK
    if any(m in low for m in GEO_MARKERS):
        return GEO
    if any(m in low for m in REACHABLE_MARKERS):
        return OK
    # Код НЕ проверяем намеренно. Первая редакция требовала 401/403 — и промахнулась
    # на живом прогоне: Google на невалидный ключ отдаёт `400 INVALID_ARGUMENT /
    # API_KEY_INVALID`, вердикт свалился в «НЕЯСНО». Признак «дело в ключе» несёт
    # тело, а не статус; статусы у вендоров разъезжаются (400 Google, 401 OpenAI,
    # 401 Anthropic).
    if any(m in low for m in KEY_MARKERS):
        return KEY
    if code == 429:
        return OK
    # Cloudflare-заглушки приходят без JSON и до провайдера: «error code: 1010»
    # (забанена подпись клиента), 1020 (правило доступа). Это не отказ вендора.
    if "error code: 10" in low:
        return CF
    return UNKNOWN


def pick_gemini_model(key: str) -> str | None:
    """Берёт первую модель, поддерживающую generateContent.

    Хардкодить имя нельзя: у Google оно меняется чаще, чем мы обновляем скрипт,
    а 404 по несуществующей модели легко принять за отказ платформы.
    """
    code, body = http(
        "https://generativelanguage.googleapis.com/v1beta/models",
        {"x-goog-api-key": key},
    )
    if not (200 <= code < 300):
        return None
    try:
        models = json.loads(body).get("models", [])
    except json.JSONDecodeError:
        return None
    for m in models:
        if "generateContent" in m.get("supportedGenerationMethods", []):
            return m["name"].split("/", 1)[-1]
    return None


# ── Проверка одного провайдера ───────────────────────────────────────────────

def run(p: Provider, force_ipv4: bool, verbose: bool) -> str:
    print(f"\n{'─' * 70}\n{p.name.upper()}  ({p.host})")

    net_ok, net_msg = check_network(p.host, force_ipv4)
    print(f"  сеть   : {'OK' if net_ok else 'FAIL'} — {net_msg}")
    if not net_ok:
        print(f"  ВЕРДИКТ: {NET}")
        return NET

    key = next((os.environ[e] for e in p.env if os.environ.get(e)), None)
    if not key:
        print(f"  ключ   : не задан ({' / '.join(p.env)})")
        print(f"  ВЕРДИКТ: {NOKEY} — сеть проходит, но гео без ключа не проверить")
        return NOKEY
    # Защита от главной ошибки первого прогона (2026-07-25): в инструкции стояло
    # `export OPENAI_API_KEY=...`, и многоточие было скопировано БУКВАЛЬНО. Все
    # четыре провайдера отчитались «ключ есть» и честно ответили «ключ невалиден».
    # Прогон впустую. Дешевле поймать здесь, чем разбирать вывод.
    if len(key) < 20 or set(key) <= set(".xX*<>"):
        print(f"  ключ   : ⚠ похоже на заглушку ({key!r}) — это не ключ, а из "
              f"примера. Гео по нему не проверить.")
        return NOKEY
    print(f"  ключ   : есть (…{key[-4:]})")

    headers = {**p.extra_headers, **p.auth(key)}

    gen_url, gen_body = p.gen_url, dict(p.gen_body)
    if p.name == "gemini":
        model = pick_gemini_model(key)
        if model:
            gen_url = (
                "https://generativelanguage.googleapis.com/v1beta/"
                f"models/{model}:generateContent"
            )
            print(f"  модель : {model} (выбрана из /v1beta/models)")
        else:
            print("  модель : список моделей недоступен → пробую дефолт")

    if p.list_url and verbose:
        code, body = http(p.list_url, headers)
        print(f"  список : HTTP {code}  ({len(body)} байт)")

    # Главная проверка: реальный вызов генерации. Именно на нём Gemini
    # возвращает 400 FAILED_PRECONDITION по локации.
    code, body = http(gen_url, headers, gen_body)
    verdict = classify(code, body)
    print(f"  вызов  : HTTP {code}")
    if verdict != OK or verbose:
        snippet = " ".join(body.split())[:400]
        print(f"  тело   : {snippet}")
    print(f"  ВЕРДИКТ: {verdict}")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description="Достижимость LLM-провайдеров с этой машины")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="проверить только указанных (gemini openai anthropic groq)")
    ap.add_argument("--ipv4", action="store_true",
                    help="принудительно IPv4 (у части хостеров гео v6 ≠ гео v4)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="печатать список моделей и тела успешных ответов")
    args = ap.parse_args()

    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        print("⚠  В окружении задан GOOGLE_CLOUD_PROJECT — он уводит google-клиенты\n"
              "   на Vertex AI и даёт ЛОЖНЫЙ гео-отказ. Этот скрипт ходит по HTTP\n"
              "   напрямую и не затронут, но при проверке через google-genai SDK\n"
              "   переменную надо снять.")

    targets = PROVIDERS
    if args.only:
        want = {n.lower() for n in args.only}
        targets = [p for p in PROVIDERS if p.name in want]
        if not targets:
            print(f"Неизвестные имена: {', '.join(want)}", file=sys.stderr)
            return 2

    results = {p.name: run(p, args.ipv4, args.verbose) for p in targets}

    print(f"\n{'═' * 70}\nИТОГ")
    for name, v in results.items():
        mark = {OK: "✅", KEY: "✅", GEO: "❌", NET: "🔌",
                NOKEY: "⚪", CF: "🛡", UNKNOWN: "❓"}[v]
        print(f"  {mark} {name:<10} {v}")

    if any(v == CF for v in results.values()):
        print("\n🛡  Cloudflare отбил запрос до провайдера — это проблема КЛИЕНТА,\n"
              "   не доступности API. Обычно виноват User-Agent. Скрипт уже слаёт\n"
              f"   свой ({USER_AGENT}); если блок остался, попробуй --ipv4 или\n"
              "   проверь этого провайдера рабочим SDK — прод-код ходит через httpx.")

    if results.get("groq") == NET:
        print("\n⚠  Groq — контрольная группа, и он недостижим по сети.\n"
              "   Значит проблема в этой машине (файрвол/DNS/маршрут), а не в\n"
              "   провайдерах. Остальные вердикты недостоверны — сначала сеть.")

    if any(v == UNKNOWN for v in results.values()):
        print("\n❓ Есть неясные вердикты: классификатор строк мог отстать от\n"
              "   формулировок вендора. Смотри тело ответа выше — оно и есть истина.")

    bad = [n for n, v in results.items() if v in (GEO, NET)]
    if bad:
        print(f"\nНедоступны: {', '.join(bad)} → варианты с ними отпадают (ADR-0017 §8/L1).")
        return 1
    print("\nВсе проверенные провайдера доступны.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
