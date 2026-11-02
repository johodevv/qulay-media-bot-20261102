"""Official Telegram Bot API server, loopback-only and in the same container."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager

from botapp.config import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def local_api_server(settings: Settings, stop: asyncio.Event):
    if not settings.embedded_api:
        yield
        return
    binary = shutil.which("telegram-bot-api")
    if not binary:
        raise ValueError(
            "telegram-bot-api dasturi o'rnatilmagan. Katta fayllar uchun Docker kerak."
        )
    await asyncio.to_thread(settings.api_dir.mkdir, parents=True, exist_ok=True)
    temporary = settings.api_dir / "temp"
    await asyncio.to_thread(temporary.mkdir, exist_ok=True)
    environment = dict(
        os.environ, TELEGRAM_API_ID=str(settings.api_id), TELEGRAM_API_HASH=settings.api_hash
    )
    process = await asyncio.create_subprocess_exec(
        binary,
        "--local",
        "--http-port=8081",
        "--http-ip-address=127.0.0.1",
        f"--dir={settings.api_dir}",
        f"--temp-dir={temporary}",
        "--verbosity=0",
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def watch():
        code = await process.wait()
        logger.error("Ichki Telegram Bot API to'xtadi: code=%s", code)
        stop.set()

    watcher = asyncio.create_task(watch())
    try:
        async with asyncio.timeout(60):
            while True:
                if process.returncode is not None:
                    raise RuntimeError("Ichki Telegram Bot API ishga tushmadi.")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", 8081)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.3)
        logger.info("Ichki Telegram Bot API tayyor. Katta fayllar uchun local mode yoqilgan.")
        yield
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 8)
            except TimeoutError:
                process.kill()
                await process.wait()
