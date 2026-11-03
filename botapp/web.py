from __future__ import annotations

import asyncio
import hmac
import json

from aiohttp import web
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application

from botapp.config import MB, Settings
from botapp.diagnostics import benchmark_ocr


def create_web_app(application: Application, settings: Settings, state: dict) -> web.Application:
    benchmark_lock = asyncio.Lock()
    handoff_lock = asyncio.Lock()

    async def health(request: web.Request) -> web.Response:
        service = state.get("service")
        workers_ok = not service or all(
            task and not task.done() for task in (service.worker, service.video_worker)
        )
        ready = state.get("ready", False) and workers_ok
        standby = state.get("standby", False)
        handoff = state.get("handoff", False)
        return web.json_response(
            {
                "status": "standby"
                if standby
                else "handoff"
                if handoff
                else "ok"
                if ready
                else "starting",
                "max_input_mb": settings.max_input_bytes // MB,
                "local_api": settings.local_mode,
                "office_enabled": bool(settings.soffice),
                "version": "0.4.4",
            },
            status=200 if ready or handoff else 503,
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
        if (
            not state.get("ready")
            or settings.mode == "standby"
            or not settings.tesseract
            or benchmark_lock.locked()
            or (service and service.busy)
        ):
            return web.json_response({"status": "deferred"}, status=409)
        async with benchmark_lock:
            result = await benchmark_ocr(settings, lambda: bool(service and service.busy))
        return web.json_response(result)

    async def handoff(request: web.Request) -> web.Response:
        supplied = request.headers.get("X-Diagnostics-Token", "")
        if not settings.webhook_secret or not hmac.compare_digest(
            supplied, settings.webhook_secret
        ):
            raise web.HTTPForbidden()
        if state.get("handoff"):
            return web.json_response({"status": "handoff"})
        if (
            settings.mode not in {"webhook", "polling"}
            or not settings.local_mode
            or not state.get("ready")
            or handoff_lock.locked()
            or benchmark_lock.locked()
        ):
            raise web.HTTPConflict()
        async with handoff_lock:
            state["ready"] = False  # New webhook deliveries retry rather than disappear.
            updater = getattr(application, "updater", None)
            polling = settings.mode == "polling" and updater and updater.running
            if polling:
                await updater.stop()

            async def restore_owner():
                if settings.mode == "polling" and updater:
                    await updater.start_polling(
                        allowed_updates=["message", "callback_query"],
                        drop_pending_updates=False,
                        bootstrap_retries=0,
                    )
                elif settings.mode == "webhook":
                    await application.bot.set_webhook(
                        url=f"{settings.webhook_url}/telegram",
                        secret_token=settings.webhook_secret,
                        allowed_updates=["message", "callback_query"],
                        max_connections=4,
                    )
                state["ready"] = True

            try:
                await asyncio.wait_for(application.update_queue.join(), timeout=15)
            except TimeoutError:
                await restore_owner()
                raise web.HTTPConflict() from None
            service = state.get("service")
            if service and service.busy:
                await restore_owner()
                raise web.HTTPConflict()
            try:
                await application.bot.delete_webhook(drop_pending_updates=False)
                await application.bot.close()
            except TelegramError:
                # A local API instance less than 10 minutes old refuses close().
                # Restore the previous owner if the handoff could not finish.
                try:
                    await restore_owner()
                except TelegramError:
                    pass
                raise web.HTTPServiceUnavailable() from None
            state["handoff"] = True
            return web.json_response({"status": "handoff"})

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
        # A handoff may begin while request.json() yields to another request.
        if not state.get("ready", False) or application.update_queue.full():
            raise web.HTTPServiceUnavailable()
        application.update_queue.put_nowait(update)
        return web.Response(text="ok")

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/diagnostics", diagnostics)
    app.router.add_post("/diagnostics/ocr-benchmark", benchmark)
    app.router.add_post("/diagnostics/handoff", handoff)
    if settings.mode == "webhook":
        app.router.add_post("/telegram", webhook)
    return app
