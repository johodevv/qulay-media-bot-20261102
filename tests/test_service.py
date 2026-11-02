import asyncio
import shutil
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pypdf import PdfReader

from botapp.handlers import Handlers
from botapp.media import MediaError
from botapp.service import ConversionService, Upload


def test_limits_user_isolation_and_stale_buttons(settings):
    service = ConversionService(replace(settings, max_input_bytes=100, max_images=2))
    session = service.add_upload(1, Upload("a", 40, "image"))
    service.add_upload(1, Upload("b", 40, "image"))
    with pytest.raises(MediaError):
        service.add_upload(1, Upload("c", 40, "image"))
    with pytest.raises(MediaError):
        service.add_upload(2, Upload("huge", 101, "image"))
    with pytest.raises(MediaError, match="eskirgan"):
        service.get_session(2, session.nonce)
    service.enqueue(1, 1, session.nonce, "pdf", 9)
    with pytest.raises(MediaError):
        service.enqueue(1, 1, session.nonce, "pdf", 9)
    assert service.queue.qsize() == 1
    assert 1 in service.busy


def test_full_queue_keeps_second_users_session(settings):
    service = ConversionService(replace(settings, max_queue=1))
    first = service.add_upload(1, Upload("a", 10, "video"))
    second = service.add_upload(2, Upload("b", 10, "image"))
    with pytest.raises(MediaError):
        service.enqueue(1, 1, first.nonce, "video-9999", 10)
    service.enqueue(1, 1, first.nonce, "video-144", 10)
    with pytest.raises(MediaError, match="Navbat to'ldi"):
        service.enqueue(2, 2, second.nonce, "pdf", 11)
    assert service.get_session(2, second.nonce) is second
    assert 2 not in service.busy


def test_sessions_expire_and_cancel_invalidates_buttons(settings):
    service = ConversionService(settings)
    session = service.add_upload(1, Upload("a", 10, "image"))
    session.touched = time.monotonic() - settings.session_ttl - 1
    with pytest.raises(MediaError):
        service.get_session(1, session.nonce)
    session = service.add_upload(1, Upload("a", 10, "image"))
    service.cancel(1)
    with pytest.raises(MediaError):
        service.get_session(1, session.nonce)


async def test_image_messages_button_worker_and_cleanup(settings, tmp_path):
    source = tmp_path / "photo.png"
    Image.new("RGB", (200, 100), "green").save(source)
    downloaded_paths = []
    sent_pdf = tmp_path / "telegram-result.pdf"

    async def download(path, **kwargs):
        downloaded_paths.append(path)
        shutil.copyfile(source, path)

    async def send_document(**kwargs):
        sent_pdf.write_bytes(kwargs["document"].read())

    bot = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=source.stat().st_size, download_to_drive=download
            )
        ),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(side_effect=send_document),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=500)),
    )
    service = ConversionService(settings)
    handlers = Handlers(service, settings, ocr_enabled=False)
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="image-a", file_size=source.stat().st_size)],
        reply_text=AsyncMock(),
        video=None,
        document=None,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
        callback_query=None,
    )
    context = SimpleNamespace(bot=bot)
    await handlers.receive(update, context)
    await handlers.receive(update, context)
    session = service.sessions[1]
    query = SimpleNamespace(
        data=f"{session.nonce}:pdf", answer=AsyncMock(), edit_message_reply_markup=AsyncMock()
    )
    update.callback_query = query
    await handlers.choose(update, context)
    await service.start(bot)
    await asyncio.wait_for(service.queue.join(), 20)
    await service.stop()
    assert len(PdfReader(sent_pdf).pages) == 2
    assert bot.send_document.await_count == 1
    assert all(not Path(path).exists() for path in downloaded_paths)
    assert 1 not in service.busy


async def test_worker_recovers_after_bad_media_and_cancelled_job(settings, tmp_path):
    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"not a picture")

    async def download(path, **kwargs):
        shutil.copyfile(invalid, path)

    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_size=13, download_to_drive=download)),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(),
    )
    service = ConversionService(settings)
    for user_id in (1, 2):
        session = service.add_upload(user_id, Upload("fake", 13, "image"))
        service.enqueue(user_id, user_id, session.nonce, "pdf", user_id)
    service.cancel(2)
    await service.start(bot)
    await asyncio.wait_for(service.queue.join(), 20)
    assert not service.busy
    assert not service.cancelled
    assert bot.get_file.await_count == 1
    assert not bot.send_document.called
    assert service.worker and not service.worker.done()
    await service.stop()


async def test_access_list_and_group_restriction(settings):
    handlers = Handlers(
        ConversionService(settings), replace(settings, allowed_user_ids=frozenset({42})), False
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(type="private"),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=None,
    )
    assert not await handlers.allowed(update)
    update.effective_user.id = 42
    assert await handlers.allowed(update)
    update.effective_chat.type = "group"
    assert not await handlers.allowed(update)


async def test_large_local_file_uses_hardlink_and_uri_without_memory_download(settings, tmp_path):
    from urllib.parse import unquote, urlparse

    api_root = tmp_path / "api"
    api_root.mkdir()
    source = api_root / "large.png"
    Image.new("RGB", (200, 100), "white").save(source)
    with source.open("ab") as stream:
        stream.truncate(25 * 1024 * 1024)
    settings = replace(
        settings,
        local_mode=True,
        api_url="http://127.0.0.1:8081",
        api_dir=api_root,
        max_input_bytes=100 * 1024 * 1024,
        max_output_bytes=100 * 1024 * 1024,
    )
    download = AsyncMock()
    results = []

    async def send_document(**kwargs):
        uri = kwargs["document"]
        assert isinstance(uri, str) and uri.startswith("file:")
        # Path.from_uri arrives in Python 3.13; support 3.12 Windows and Linux.
        path = unquote(urlparse(uri).path)
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        results.append(len(PdfReader(path).pages))

    bot = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=source.stat().st_size, file_path=str(source), download_to_drive=download
            )
        ),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(side_effect=send_document),
    )
    service = ConversionService(settings)
    session = service.add_upload(1, Upload("large", source.stat().st_size, "image"))
    service.enqueue(1, 1, session.nonce, "pdf", 1)
    await service.start(bot)
    await asyncio.wait_for(service.queue.join(), 20)
    await service.stop()
    download.assert_not_called()
    assert results == [1]
    assert not source.exists()


async def test_document_is_not_blocked_by_video(settings, tmp_path, monkeypatch):
    from botapp.media import VideoInfo

    source = tmp_path / "source.png"
    Image.new("RGB", (200, 100), "green").save(source)
    video_started, release_video = asyncio.Event(), asyncio.Event()
    sent = []

    async def convert(source, output, resolution, settings, on_progress=None):
        video_started.set()
        await release_video.wait()
        output.write_bytes(b"video")
        return VideoInfo(256, 144, 1, False)

    async def download(path, **kwargs):
        shutil.copyfile(source, path)

    async def send(**kwargs):
        sent.append(kwargs["filename"])

    monkeypatch.setattr("botapp.service.convert_video", convert)
    bot = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=source.stat().st_size, download_to_drive=download
            )
        ),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(side_effect=send),
    )
    service = ConversionService(settings)
    video = service.add_upload(1, Upload("video", 1000, "video", 1280, 720))
    service.enqueue(1, 1, video.nonce, "video-144", 1)
    document = service.add_upload(2, Upload("image", source.stat().st_size, "image"))
    service.enqueue(2, 2, document.nonce, "pdf", 2)
    await service.start(bot)
    try:
        await asyncio.wait_for(video_started.wait(), 10)
        await asyncio.wait_for(service.queue.join(), 10)
        assert sent == ["natija.pdf"]
        assert "0%" in service.status_text(1)
    finally:
        release_video.set()
        await asyncio.wait_for(service.video_queue.join(), 10)
        await service.stop()


async def test_cancelling_video_keeps_worker_alive(settings, tmp_path, monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    source = tmp_path / "source"
    source.write_bytes(b"source")

    async def convert(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def download(path, **kwargs):
        shutil.copyfile(source, path)

    monkeypatch.setattr("botapp.service.convert_video", convert)
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_size=6, download_to_drive=download)),
        edit_message_text=AsyncMock(),
        send_document=AsyncMock(),
    )
    service = ConversionService(settings)
    session = service.add_upload(1, Upload("a", 6, "video", 1280, 720))
    service.enqueue(1, 1, session.nonce, "video-144", 1)
    await service.start(bot)
    try:
        await asyncio.wait_for(started.wait(), 10)
        service.cancel(1)
        await asyncio.wait_for(stopped.wait(), 2)
        await asyncio.wait_for(service.video_queue.join(), 2)
        assert service.video_worker and not service.video_worker.done()
        assert 1 not in service.busy
        bot.send_document.assert_not_called()
    finally:
        await service.stop()
