from __future__ import annotations

import asyncio
import hmac
import json

from aiohttp import web
from telegram import Update
from telegram.ext import Application

from botapp.config import MB, Settings
from botapp.diagnostics import benchmark_ocr


def create_web_app(application: Application, settings: Settings, state: dict) -> web.Application:
    benchmark_lock = asyncio.Lock()

    async def health(request: web.Request) -> web.Response:
        service = state.get("service")
        workers_ok = not service or all(
            task and not task.done() for task in (service.worker, service.video_worker)
        )
        ready = state.get("ready", False) and workers_ok
        return web.json_response(
            {
                "status": "ok" if ready else "starting",
                "max_input_mb": settings.max_input_bytes // MB,
                "local_api": settings.local_mode,
                "office_enabled": bool(settings.soffice),
                "version": "0.4.1",
            },
            status=200 if ready else 503,
        )

    async def diagnostics(request: web.Request) -> web.Response:
        supplied = request.headers.get("X-Diagnostics-Token", "")
        if not settings.webhook_secret or not hmac.compare_digest(
            supplied, settings.webhook_secret
        ):
            raise web.HTTPForbidden()
        service = state.get("service")
        return web.json_response({"performance": service.performance() if service else {}})

    async def benchmark(request: web.Request) -> web.Response:
        supplied = request.headers.get("X-Diagnostics-Token", "")
        if not settings.webhook_secret or not hmac.compare_digest(
            supplied, settings.webhook_secret
        ):
            raise web.HTTPForbidden()
        service = state.get("service")
        if not settings.tesseract or benchmark_lock.locked() or (service and service.busy):
            return web.json_response({"status": "deferred"}, status=409)
        async with benchmark_lock:
            result = await benchmark_ocr(settings, lambda: bool(service and service.busy))
        return web.json_response(result)

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
    app.router.add_get("/diagnostics", diagnostics)
    app.router.add_post("/diagnostics/ocr-benchmark", benchmark)
    if settings.mode == "webhook":
        app.router.add_post("/telegram", webhook)
    return app
