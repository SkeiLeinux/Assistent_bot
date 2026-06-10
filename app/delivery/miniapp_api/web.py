"""Сборка миниапы: общий auth/error-middleware + монтаж kernel/lists/finance/nutrition.

Волна A (Срез 2): миниапа отдаёт kernel REST (/api/v1/{me,family,...}) + Lists REST
(/api/v1/lists,/items) + Finance REST (/api/v1/finance/*) + Nutrition REST
(/api/v1/nutrition/*). Старый food api.py удалён — функциональность в
contexts.nutrition.api.
"""

import logging

from aiohttp import web

from app.config import settings
from app.db.engine import async_session_maker
from app.kernel.auth import InitDataError, resolve_viewer
from app.kernel.services.errors import KernelError, NotWhitelisted
from app.kernel.sharing import VisibilityError

from app.contexts.lists.api import register_routes as register_list_routes
from app.contexts.lists.services import ListError
from app.contexts.finance.api import register_routes as register_finance_routes
from app.contexts.finance.services import FinanceError
from app.contexts.nutrition.api import register_routes as register_nutrition_routes
from app.contexts.nutrition.services import (
    FamilyRequired as NutritionFamilyRequired,
    InvalidInput as NutritionInvalidInput,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    NutritionError,
)
from app.delivery.miniapp_api._http import error_response
from app.delivery.miniapp_api.kernel_api import register_kernel_routes

logger = logging.getLogger(__name__)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Резолв зрителя из initData; сессия живёт на время запроса, commit при успехе."""
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    async with async_session_maker() as session:
        try:
            user, viewer = await resolve_viewer(session, init_data)
        except InitDataError as exc:
            return error_response(f"Авторизация не прошла: {exc}", "auth", 401)
        except NotWhitelisted as exc:
            return error_response(exc.message, exc.code, exc.status)

        request["session"] = session
        request["user"] = user
        request["viewer"] = viewer
        response = await handler(request)
        await session.commit()
        return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Доменные/LLM-ошибки → аккуратный JSON с кодом/статусом (kernel/lists/finance/nutrition)."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    # kernel/lists/finance несут message/code/status (единый контракт ошибок).
    except (KernelError, ListError, FinanceError) as exc:
        return error_response(exc.message, exc.code, exc.status)
    except VisibilityError as exc:
        return error_response(str(exc), "invalid_visibility", 422)
    # Nutrition: доменные ошибки — message через str(exc) (Q-nut-3).
    except NutritionInvalidInput as exc:
        return error_response(str(exc), "invalid_input", 422)
    except NutritionFamilyRequired as exc:
        return error_response(str(exc) or "Нужна семья", "family_required", 409)
    except NutritionError as exc:
        return error_response(str(exc), "nutrition_error", 422)
    # LLM-ошибки (подклассы — раньше базового LLMError): 503 / 429 / 502.
    except LLMNotConfiguredError as exc:
        return error_response(str(exc) or "Модуль расчёта КБЖУ не настроен", "llm_unconfigured", 503)
    except LLMRateLimitError as exc:
        return error_response(str(exc) or "Превышен лимит запросов к модели", "llm_rate_limit", 429)
    except LLMError as exc:
        return error_response(str(exc) or "Ошибка сервиса расчёта КБЖУ", "llm_error", 502)
    except Exception as exc:  # noqa: BLE001 — последний рубеж
        logger.exception("Необработанная ошибка в API миниапы: %s", exc)
        return error_response("Внутренняя ошибка сервера", "server", 500)


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    register_kernel_routes(app)
    register_list_routes(app)
    register_finance_routes(app)
    register_nutrition_routes(app)
    return app


async def start_web_server() -> web.AppRunner:
    """Поднять aiohttp (kernel + lists REST) в текущем event-loop. Возвращает runner."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    logger.info(
        "Веб-сервер мини-апы (kernel + lists + finance + nutrition) слушает http://%s:%s",
        settings.webapp_host,
        settings.webapp_port,
    )
    return runner
