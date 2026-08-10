"""`GET /api/v1/health` — что РЕАЛЬНО загрузилось в задеплоенном процессе.

Зачем эндпоинт существует. Выкатка 10.08 закончилась строкой «сервис активен», а
02.08 краш-луп при `Restart=always` выглядел ровно так же. Без ответа на вопрос
«какой коммит и какая модель сейчас в процессе» смоук не отличает «выкачен новый
код» от «процесс поднялся на старом» — то есть проверяет не то, ради чего его
запускают.

Три правила, каждое под тестом (`tests/test_health.py`):

1. **Ни одного захардкоженного значения.** Смысл ответа — показать, что
   загрузилось, а константа в теле показывала бы то, что написано в коде.
2. **Ничего чувствительного.** Ни ключа, ни токена, ни путей, ни версий
   библиотек, ни полей `.env` сверх перечисленных: эндпоинт публичный по факту
   (он вне auth, иначе смоук не сможет проверить версию до того, как убедится,
   что auth работает, и перепутает два разных отказа).
3. **Health не роняет процесс.** Нет `.git` или он битый → `commit: "unknown"`.
   Health, из-за которого сервис не поднялся, хуже отсутствующего health.
"""

from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from app.config import settings
from app.delivery.miniapp_api._http import json_response

HEALTH_PATH = "/api/v1/health"

UNKNOWN_COMMIT = "unknown"
SHA_LEN = 7

# app/delivery/miniapp_api/health.py → корень репозитория.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_git_dir(repo_root: Path) -> Path | None:
    """Каталог `.git`. У worktree/submodule это файл с `gitdir: <путь>`."""
    git = repo_root / ".git"
    if git.is_dir():
        return git
    if git.is_file():
        text = git.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            target = Path(text.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = (repo_root / target).resolve()
            return target if target.is_dir() else None
    return None


def read_commit(repo_root: Path = _REPO_ROOT) -> str:
    """Короткий SHA текущего HEAD, прочитанный ИЗ ФАЙЛОВ `.git`.

    Не `subprocess` с `git`: деплой — это `git pull` в `/opt/Assistent_bot`, то
    есть `.git` заведомо на месте, а вызов внешнего бинаря на каждый запрос к
    health добавил бы зависимость от него и лишний вектор в публичный эндпоинт.

    Любая неожиданность (нет каталога, битый ref, нет прав) — это `"unknown"`, а
    не исключение: см. правило 3 в шапке модуля.
    """
    try:
        git_dir = _resolve_git_dir(repo_root)
        if git_dir is None:
            return UNKNOWN_COMMIT
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            # detached HEAD — в самом файле лежит SHA
            return _short(head)
        ref = head.split(":", 1)[1].strip()
        loose = git_dir / Path(ref)
        if loose.is_file():
            return _short(loose.read_text(encoding="utf-8").strip())
        # Свежий клон держит ветки в packed-refs, отдельного файла ref может не быть.
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return _short(sha)
        return UNKNOWN_COMMIT
    except OSError:
        return UNKNOWN_COMMIT


def _short(sha: str) -> str:
    sha = sha.strip()
    if len(sha) < SHA_LEN or not all(c in "0123456789abcdefABCDEF" for c in sha):
        return UNKNOWN_COMMIT
    return sha[:SHA_LEN]


# Коммит читается ОДИН раз, при импорте, и это не оптимизация.
#
# Питон не перечитывает исходники после `git pull`, поэтому работающий процесс
# несёт тот код, с которым стартовал. Читай мы `.git` на каждый запрос — health
# после `git pull` без рестарта отрапортовал бы НОВЫЙ коммит, работая на старом
# коде, то есть соврал бы ровно в том сценарии, ради которого заведён.
COMMIT = read_commit()
STARTED_AT = datetime.now(timezone.utc).astimezone()

# Состав ответа — явным списком, а не «что получилось». Публичный эндпоинт растёт
# по одному полю за раз, и без списка однажды в него попадёт путь, версия
# библиотеки или содержимое `.env`; тест сверяет ключи именно с этим набором.
HEALTH_FIELDS = (
    "commit",
    "model",
    "fallback_model",
    "api_max_images",
    "llm_max_images",
    "started_at",
)


def health_payload() -> dict:
    """Тело ответа. Чистая функция — тестируется без сервера."""
    return {
        "commit": COMMIT,
        # Имена полей конфига: `llm_vision_model`, не `llm_model` (ADR-0017 §16).
        "model": settings.llm_vision_model,
        "fallback_model": settings.llm_fallback_vision_model,
        "api_max_images": settings.api_max_images,
        "llm_max_images": settings.llm_max_images,
        "started_at": STARTED_AT.isoformat(timespec="seconds"),
    }


async def health(request: web.Request) -> web.Response:
    return json_response(health_payload())


def register_health_routes(app: web.Application) -> None:
    app.router.add_get(HEALTH_PATH, health)
