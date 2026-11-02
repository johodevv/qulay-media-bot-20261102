import shutil
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw, ImageFont
from telegram.error import BadRequest, TimedOut
from telegram.ext import Application

from botapp.diagnostics import benchmark_ocr
from botapp.document_layout import font_path, ink_and_background
from botapp.media import ocr_available
from botapp.service import CachedResult, ConversionService, Job, Upload
from botapp.web import create_web_app


@pytest.mark.parametrize("color", [(0, 0, 0), (100, 100, 100), (25, 90, 200), (200, 25, 25)])
def test_ink_keeps_glyph_color_and_paper(color):
    image = Image.new("RGB", (400, 100), (240, 240, 240))
    font = ImageFont.truetype(str(font_path()), 36)
    ImageDraw.Draw(image).text((20, 20), "HUJJAT 12345", fill=color, font=font)
    ink, paper = ink_and_background(image, (20, 25, 270, 35))
    assert paper == (240, 240, 240)
    assert max(abs(old - new) for old, new in zip(color, ink, strict=True)) < 10
    assert ink_and_background(image, (320, 20, 40, 20)) == (paper, paper)


async def test_repeated_file_reuses_telegram_result_without_download_or_conversion(
    settings, tmp_path
):
    source = tmp_path / "source.png"
    Image.new("RGB", (200, 100), "blue").save(source)

    async def download(path, **kwargs):
        shutil.copyfile(source, path)

    bot = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=source.stat().st_size, download_to_drive=download
            )
        ),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(
            return_value=SimpleNamespace(document=SimpleNamespace(file_id="result"))
        ),
    )
    service = ConversionService(settings)
    upload = Upload("input", source.stat().st_size, "image")
    for _ in range(2):
        await service.execute(bot, Job(1, 1, (upload,), "pdf", 10))
    assert bot.get_file.await_count == 1
    assert bot.send_document.call_args.kwargs["document"] == "result"
    assert service.performance()["pdf"]["cached"] == 1
    # Another user's output must never be reused from this user's cache.
    await service.execute(bot, Job(2, 2, (upload,), "pdf", 11))
    assert bot.get_file.await_count == 2
    first = Job(1, 1, (upload,), "pdf", 10)
    service.results[service.result_key(first)] = CachedResult("expired", "caption", "a.pdf", 0)
    assert service.cached_result(first) is None


async def test_expired_telegram_file_reference_falls_back_to_conversion(settings, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (200, 100), "blue").save(source)

    async def download(path, **kwargs):
        shutil.copyfile(source, path)

    bot = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=source.stat().st_size, download_to_drive=download
            )
        ),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(
            side_effect=[
                BadRequest("file reference expired"),
                SimpleNamespace(document=SimpleNamespace(file_id="new-result")),
            ]
        ),
    )
    service = ConversionService(settings)
    job = Job(1, 1, (Upload("input", 100, "image"),), "pdf", 10)
    service.results[service.result_key(job)] = CachedResult(
        "old", "caption", "a.pdf", time.monotonic() + 30
    )
    await service.execute(bot, job)
    assert bot.get_file.await_count == 1 and bot.send_document.await_count == 2
    assert service.cached_result(job).file_id == "new-result"


async def test_network_timeout_does_not_waste_time_reencoding_cached_file(settings):
    service = ConversionService(settings)
    job = Job(1, 1, (Upload("input", 100, "video"),), "video-144", 10)
    service.results[service.result_key(job)] = CachedResult(
        "result", "caption", "a.mp4", time.monotonic() + 30
    )
    bot = SimpleNamespace(
        send_document=AsyncMock(side_effect=TimedOut()),
        get_file=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    with pytest.raises(TimedOut):
        await service.execute(bot, job)
    bot.get_file.assert_not_awaited()


async def test_diagnostics_is_authenticated_and_contains_no_user_or_file_ids(settings):
    service = ConversionService(settings)
    service.timings.append(
        {
            "action": "pdf",
            "cached": False,
            "queue_s": 0,
            "download_s": 0.1,
            "convert_s": 2,
            "upload_s": 0.2,
        }
    )
    settings = replace(settings, webhook_secret="a" * 32)
    application = Application.builder().token(settings.token).build()
    app = create_web_app(application, settings, {"ready": True, "service": service})
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/diagnostics")).status == 403
        assert (await client.post("/diagnostics/ocr-benchmark")).status == 403
        response = await client.get("/diagnostics", headers={"X-Diagnostics-Token": "a" * 32})
        assert response.status == 200
        data = await response.json()
        assert data["performance"]["pdf"]["convert_s"] == 2
        assert "user_id" not in str(data) and "file_id" not in str(data)
        service.busy.add(1)
        response = await client.post(
            "/diagnostics/ocr-benchmark", headers={"X-Diagnostics-Token": "a" * 32}
        )
        assert response.status == 409


async def test_fixed_input_ocr_comparison_and_busy_preemption(settings):
    if not ocr_available(settings):
        pytest.skip("Tesseract is required")
    result = await benchmark_ocr(settings, lambda: False)
    assert result["status"] == "ok"
    assert all(row["same_text"] for row in result["results"])
    assert [row["omp_threads"] for row in result["results"]] == [4, 1]
    assert (await benchmark_ocr(settings, lambda: True))["status"] == "deferred"
