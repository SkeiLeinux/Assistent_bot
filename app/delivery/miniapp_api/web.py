"""Сборка миниапы: общий auth/error-middleware + монтаж kernel- и lists-маршрутов.

Срез 2: миниапа отдаёт kernel REST (/api/v1/{me,family,...}) + Lists REST
(/api/v1/lists,/items). Эндпоинты еды (api.py) — до миграции Nutrition (S2c).
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
    """Доменные ошибки (kernel/lists/видимость) → аккуратный JSON с кодом/статусом."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (KernelError, ListError) as exc:
        return error_response(exc.message, exc.code, exc.status)
    except VisibilityError as exc:
        return error_response(str(exc), "invalid_visibility", 422)
    except Exception as exc:  # noqa: BLE001 — последний рубеж
        logger.exception("Необработанная ошибка в API миниапы: %s", exc)
        return error_response("Внутренняя ошибка сервера", "server", 500)


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    register_kernel_routes(app)
    register_list_routes(app)
    return app


async def start_web_server() -> web.AppRunner:
    """Поднять aiohttp (kernel + lists REST) в текущем event-loop. Возвращает runner."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    logger.info(
        "Веб-сервер мини-апы (kernel + lists) слушает http://%s:%s",
        settings.webapp_host,
        settings.webapp_port,
    )
    return runner
