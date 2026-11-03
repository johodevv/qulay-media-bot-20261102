from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import AsyncExitStack

from aiohttp import web
from telegram import BotCommand
from telegram.ext import Application

from botapp.config import Settings
from botapp.handlers import Handlers
from botapp.local_api import local_api_server
from botapp.media import ocr_available
from botapp.service import ConversionService
from botapp.web import create_web_app

logger = logging.getLogger(__name__)


async def serve(settings: Settings) -> None:
    if settings.mode == "standby":
        await serve_standby(settings)
        return
    builder = (
        Application.builder()
        .token(settings.token)
        .update_queue(asyncio.Queue(maxsize=100))
        .read_timeout(60)
        .write_timeout(120)
        .get_updates_read_timeout(40)
    )
    if settings.api_url:
        builder.base_url(f"{settings.api_url}/bot").base_file_url(f"{settings.api_url}/file/bot")
        builder.local_mode(settings.local_mode)
    application = builder.build()
    service = ConversionService(settings)
    ocr_enabled = await asyncio.to_thread(ocr_available, settings)
    Handlers(service, settings, ocr_enabled).register(application)
    state = {"ready": False, "service": service}
    runner = web.AppRunner(create_web_app(application, settings, state), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.port).start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(local_api_server(settings, stop))
            await stack.enter_async_context(application)
            await application.bot.set_my_commands(
                [
                    BotCommand("start", "Boshlash va yo'riqnoma"),
                    BotCommand("menu", "Xizmatlar menyusini ochish"),
                    BotCommand("status", "Topshiriq holati"),
                    BotCommand("cancel", "Tanlovni bekor qilish"),
                    BotCommand("privacy", "Fayllar qanday saqlanadi"),
                ]
            )
            await service.start(application.bot)
            await application.start()
            try:
                if settings.mode == "webhook":
                    await application.bot.set_webhook(
                        url=f"{settings.webhook_url}/telegram",
                        secret_token=settings.webhook_secret,
                        allowed_updates=["message", "callback_query"],
                        max_connections=4,
                    )
                else:
                    await application.bot.delete_webhook(drop_pending_updates=False)
                    await application.updater.start_polling(
                        allowed_updates=["message", "callback_query"],
                        drop_pending_updates=False,
                        bootstrap_retries=0,
                    )
                state["ready"] = True
                logger.info("Bot ishga tushdi. Rejim: %s. OCR: %s", settings.mode, ocr_enabled)
                await stop.wait()
            finally:
                state["ready"] = False
                await runner.cleanup()
                if application.updater.running:
                    await application.updater.stop()
                await application.stop()
                await service.stop()
    finally:
        state["ready"] = False
        await runner.cleanup()


async def serve_standby(settings: Settings) -> None:
    """Keep the former host observable without logging the bot in twice."""
    application = Application.builder().token(settings.token).build()
    runner = web.AppRunner(
        create_web_app(application, settings, {"ready": True, "standby": True}),
        access_log=None,
    )
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.port).start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    logger.info("Zaxira rejimi: bu server Telegram botini ishga tushirmaydi.")
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Qulay Media Telegram Bot")
    parser.add_argument(
        "--check", action="store_true", help="Token ishlatmasdan vositalarni tekshirish"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # HTTP klient odatiy loglari Telegram URL'idagi tokenni ko'rsatishi mumkin.
    for name in ("httpx", "httpcore", "telegram", "aiohttp.access"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        settings = Settings.from_env(require_token=not args.check)
        if args.check:
            print(f"FFmpeg: {'OK' if settings.ffmpeg else 'TOPILMADI'}")
            print(f"FFprobe: {'OK' if settings.ffprobe else 'TOPILMADI'}")
            print(f"OCR va tillar: {'OK' if ocr_available(settings) else 'TOPILMADI'}")
            print(f"Telegram tokeni: {'YOZILGAN' if settings.token else 'YOZILMAGAN'}")
            return
        asyncio.run(serve(settings))
    except ValueError as exc:
        parser.exit(1, f"Sozlama xatosi: {exc}\n")
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi.")
    except Exception as exc:
        # Token yoki xizmat hujjati tafsilotlarini traceback orqali chiqarmaymiz.
        logger.error(
            "Ishga tushish xatosi: %s. Token, internet va sozlamalarni tekshiring.",
            type(exc).__name__,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
