from __future__ import annotations

import hmac
import json

from aiohttp import web
from telegram import Update
from telegram.ext import Application

from botapp.config import Settings


def create_web_app(application: Application, settings: Settings, state: dict) -> web.Application:
    async def health(request: web.Request) -> web.Response:
        service = state.get("service")
        workers_ok = not service or all(
            task and not task.done() for task in (service.worker, service.video_worker)
        )
        ready = state.get("ready", False) and workers_ok
        return web.json_response(
            {"status": "ok" if ready else "starting"}, status=200 if ready else 503
        )

    async def webhook(request: web.Request) -> web.Response:
        if not state.get("ready", False):
            raise web.HTTPServiceUnavailable()
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied, settings.webhook_secret):
            raise web.HTTPForbidden()
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("update_id"), int):
                raise ValueError
            update = Update.de_json(data, application.bot)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise web.HTTPBadRequest() from exc
        if application.update_queue.full():
            raise web.HTTPServiceUnavailable()
        application.update_queue.put_nowait(update)
        return web.Response(text="ok")

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    if settings.mode == "webhook":
        app.router.add_post("/telegram", webhook)
    return app
