"""Общие HTTP-хелперы миниапы (ru-JSON, ошибки, разбор тела). Лист без доменных импортов."""

import json

from aiohttp import web


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_response(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def error_response(message: str, code: str, status: int) -> web.Response:
    return json_response({"code": code, "message": message}, status=status)


async def json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}
