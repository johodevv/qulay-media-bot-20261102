import asyncio
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer
from telegram.ext import Application

from botapp.web import create_web_app


async def test_health_webhook_secret_payload_and_backpressure(settings):
    settings = replace(settings, mode="webhook", webhook_secret="a" * 32)
    application = (
        Application.builder().token(settings.token).update_queue(asyncio.Queue(maxsize=1)).build()
    )
    state = {"ready": False}
    async with TestClient(TestServer(create_web_app(application, settings, state))) as client:
        assert (await client.get("/health")).status == 503
        state["ready"] = True
        assert (await client.get("/health")).status == 200
        assert (await client.post("/telegram", json={"update_id": 1})).status == 403
        headers = {"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret}
        assert (await client.post("/telegram", json=[], headers=headers)).status == 400
        assert (
            await client.post("/telegram", json={"update_id": 1}, headers=headers)
        ).status == 200
        assert application.update_queue.qsize() == 1
        assert (
            await client.post("/telegram", json={"update_id": 2}, headers=headers)
        ).status == 503


async def test_polling_mode_has_no_webhook_route(settings):
    application = Application.builder().token(settings.token).build()
    async with TestClient(
        TestServer(create_web_app(application, settings, {"ready": True}))
    ) as client:
        assert (await client.post("/telegram", json={})).status == 404
