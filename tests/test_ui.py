from types import SimpleNamespace
from unittest.mock import AsyncMock

from botapp.handlers import Handlers, image_keyboard, video_keyboard
from botapp.service import ConversionService
from botapp.ui import PDF, VIDEO, WORD, main_keyboard, progress_text


def test_main_keyboard_can_toggle_with_native_icon_and_video_menu_only_reduce():
    keyboard = main_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert keyboard.is_persistent is False and keyboard.resize_keyboard
    assert keyboard.one_time_keyboard is False
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
    assert choices[0][0].callback_data == "nonce:video-fast"
    assert choices[1][0].callback_data == "nonce:video-0"


def test_truthful_progress_has_no_fake_percentage_during_transfer():
    assert "42%" in progress_text("Kichraytirilmoqda", 42, 65)
    assert "1:05" in progress_text("Kichraytirilmoqda", 42, 65)
    assert "%" not in progress_text("Yuborilmoqda", None, 10)
    assert "⏳" in progress_text("Yuborilmoqda", None, 10)


def test_document_menu_has_one_main_action_and_secondary_options():
    for mode, action in (("pdf", "pdf-edit"), ("word", "word-layout")):
        buttons = [
            button for row in image_keyboard("n", True, mode).inline_keyboard for button in row
        ]
        assert buttons[0].callback_data == f"n:{action}"
        assert "tayyorlash" in buttons[0].text and "OCR" not in buttons[0].text
        assert buttons[-1].callback_data == "n:menu-more"
        if mode == "word":
            assert buttons[1].callback_data == "n:text"


async def test_opening_and_closing_other_options_keeps_uploads_out_of_queue(settings):
    from botapp.service import Upload

    service = ConversionService(settings)
    service.select_service(1, "word")
    session = service.add_upload(1, Upload("image", 100, "image"))
    handlers = Handlers(service, settings, True)
    query = SimpleNamespace(
        data=f"{session.nonce}:menu-more", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        callback_query=query,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await handlers.choose(update, context)
    buttons = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in buttons] == [
        f"{session.nonce}:text",
        f"{session.nonce}:word",
        f"{session.nonce}:menu-back",
    ]
    query.data = f"{session.nonce}:menu-back"
    await handlers.choose(update, context)
    buttons = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert buttons[0][0].callback_data.endswith(":word-layout")
    assert service.sessions[1] is session and len(session.uploads) == 1
    assert service.queue.empty() and not service.busy
    context.bot.send_message.assert_not_awaited()


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


async def test_menu_reopens_keyboard_without_clearing_selected_images(settings):
    from botapp.service import Upload

    service = ConversionService(settings)
    session = service.add_upload(1, Upload("image", 100, "image"))
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
        callback_query=None,
    )
    await Handlers(service, settings, True).menu(update, SimpleNamespace())
    assert service.sessions[1] is session and len(session.uploads) == 1
    keyboard = message.reply_text.call_args.kwargs["reply_markup"]
    assert keyboard.is_persistent is False and keyboard.one_time_keyboard is False
