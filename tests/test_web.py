import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from telegram.error import RetryAfter
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


async def test_handoff_requires_auth_idle_workers_and_rejects_new_updates(settings):
    settings = replace(
        settings,
        mode="webhook",
        local_mode=True,
        webhook_secret="a" * 32,
        webhook_url="https://example.test",
    )
    bot = SimpleNamespace(delete_webhook=AsyncMock(), close=AsyncMock(), set_webhook=AsyncMock())
    application = SimpleNamespace(bot=bot, update_queue=asyncio.Queue())
    worker = SimpleNamespace(done=lambda: False)
    service = SimpleNamespace(busy={1}, worker=worker, video_worker=worker)
    state = {"ready": True, "service": service}
    headers = {"X-Diagnostics-Token": settings.webhook_secret}
    async with TestClient(TestServer(create_web_app(application, settings, state))) as client:
        assert (await client.post("/diagnostics/handoff")).status == 403
        assert (await client.post("/diagnostics/handoff", headers=headers)).status == 409
        assert state["ready"]
        bot.close.assert_not_awaited()
        service.busy.clear()
        assert (await client.post("/diagnostics/handoff", headers=headers)).status == 200
        bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        bot.close.assert_awaited_once()
        assert not state["ready"]
        assert (await client.get("/health")).status == 200
        assert (await (await client.get("/health")).json())["status"] == "handoff"
        assert (await client.post("/telegram", json={"update_id": 1})).status == 503
        assert (await client.post("/diagnostics/handoff", headers=headers)).status == 200
        bot.close.assert_awaited_once()


async def test_handoff_restores_webhook_when_local_api_refuses_close(settings):
    settings = replace(
        settings,
        mode="webhook",
        local_mode=True,
        webhook_secret="a" * 32,
        webhook_url="https://example.test",
    )
    bot = SimpleNamespace(
        delete_webhook=AsyncMock(),
        close=AsyncMock(side_effect=RetryAfter(60)),
        set_webhook=AsyncMock(),
    )
    application = SimpleNamespace(bot=bot, update_queue=asyncio.Queue())
    state = {"ready": True}
    async with TestClient(TestServer(create_web_app(application, settings, state))) as client:
        response = await client.post(
            "/diagnostics/handoff", headers={"X-Diagnostics-Token": settings.webhook_secret}
        )
        assert response.status == 503
        assert state["ready"] and not state.get("handoff")
        bot.set_webhook.assert_awaited_once()


async def test_standby_is_observable_but_cannot_take_over_telegram(settings):
    settings = replace(settings, mode="standby", webhook_secret="a" * 32)
    settings.validate()
    application = Application.builder().token(settings.token).build()
    async with TestClient(
        TestServer(create_web_app(application, settings, {"ready": True, "standby": True}))
    ) as client:
        assert (await (await client.get("/health")).json())["status"] == "standby"
        assert (await client.post("/telegram", json={"update_id": 1})).status == 404
        assert (
            await client.post(
                "/diagnostics/handoff", headers={"X-Diagnostics-Token": settings.webhook_secret}
            )
        ).status == 409
