from types import SimpleNamespace
from unittest.mock import AsyncMock

from botapp.handlers import Handlers, video_keyboard
from botapp.service import ConversionService
from botapp.ui import PDF, VIDEO, WORD, main_keyboard, progress_text


def test_persistent_main_keyboard_and_video_menu_only_reduce():
    keyboard = main_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert keyboard.is_persistent and keyboard.resize_keyboard
    assert all(label in labels for label in (VIDEO, PDF, WORD))
    choices = [
        button.callback_data
        for row in video_keyboard("nonce", 720).inline_keyboard
        for button in row
    ]
    assert "nonce:video-360" in choices
    assert "nonce:video-0" in choices
    assert not any(
        choice in choices for choice in ("nonce:video-720", "nonce:video-1080", "nonce:video-2160")
    )
    choices = video_keyboard("nonce", 144).inline_keyboard
    assert len(choices) == 1 and choices[0][0].callback_data == "nonce:video-0"


def test_truthful_progress_has_no_fake_percentage_during_transfer():
    assert "42%" in progress_text("Kichraytirilmoqda", 42, 65)
    assert "1:05" in progress_text("Kichraytirilmoqda", 42, 65)
    assert "%" not in progress_text("Yuborilmoqda", None, 10)
    assert "⏳" in progress_text("Yuborilmoqda", None, 10)


async def test_service_selected_before_upload(settings):
    service = ConversionService(settings)
    handlers = Handlers(service, settings, False)
    message = SimpleNamespace(
        text=PDF, reply_text=AsyncMock(), photo=None, video=None, document=None
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
        callback_query=None,
    )
    await handlers.receive(update, SimpleNamespace(bot=None))
    assert service.sessions[1].mode == "pdf"
    assert not service.sessions[1].uploads
    message.text = None
    message.photo = [SimpleNamespace(file_id="a", file_size=100, width=200, height=100)]
    await handlers.receive(update, SimpleNamespace(bot=None))
    kwargs = message.reply_text.call_args.kwargs
    assert len(kwargs["reply_markup"].inline_keyboard) == 1
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data.endswith(":pdf")
