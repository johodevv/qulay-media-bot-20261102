import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import fitz
import httpx
import pytest

from botapp import gemini_quiz
from botapp.handlers import Handlers
from botapp.media import MediaError
from botapp.service import ConversionService
from botapp.store import BotStore
from botapp.ui import AI_QUIZ

EVIDENCE = "Bir hafta yetti kundan iborat."
SOURCE = (
    f"{EVIDENCE} Kunlar ketma-ket birga hafta deb ataladi. "
    "Bir kun yigirma to'rt soat. Mashq uchun vaqt o'lchov birliklari ko'rib chiqiladi."
)
ITEM = {
    "question": "Bir hafta necha kun?",
    "options": ["Olti", "Yetti", "Sakkiz", "To'qqiz"],
    "correct": 1,
    "page": 1,
    "evidence": EVIDENCE,
}


def mock_gemini(monkeypatch, draft, checks, *, status=200, first_unavailable=False):
    calls = []

    def respond(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "test-only-key"
        assert not request.url.query
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "tools" not in body
        calls.append(json.loads(body["contents"][0]["parts"][0]["text"]))
        if first_unavailable and len(calls) == 1:
            return httpx.Response(503)
        if status != 200:
            return httpx.Response(status)
        output = draft if len(calls) == 1 + first_unavailable else checks
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": json.dumps(output)}],
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(gemini_quiz.httpx, "AsyncClient", lambda **kwargs: client)
    return calls


@pytest.mark.asyncio
async def test_gemini_filters_unverified_questions_and_does_not_leak_draft_key(
    settings, monkeypatch
):
    settings = replace(settings, gemini_api_key="test-only-key")
    calls = mock_gemini(
        monkeypatch,
        {
            "questions": [
                ITEM,
                {
                    **ITEM,
                    "question": "Vaqt o'lchov birliklari nima?",
                    "evidence": "Uydirma manba matni",
                },
                {**ITEM, "question": "Haftaning davomiyligi?", "correct": 0},
                {**ITEM, "question": "Bir haftadagi kunlar soni?", "options": ["1", "1", "2", "3"]},
            ]
        },
        {"checks": [{"id": 1, "correct": 1}, {"id": 3, "correct": 1}]},
    )
    reserve, review = Mock(), AsyncMock()
    result = await gemini_quiz.generate_pdf_quiz([SOURCE], settings, reserve, review)
    assert [q["question"] for q in result] == [ITEM["question"]]
    assert result[0]["source_answer"].endswith(EVIDENCE)
    assert len(calls) == 2 and [q["id"] for q in calls[1]["items"]] == [1, 3]
    assert all("correct" not in item for item in calls[1]["items"])
    reserve.assert_called_once()
    review.assert_awaited_once()


@pytest.mark.asyncio
async def test_gemini_rate_limit_uses_readable_error(settings, monkeypatch):
    settings = replace(settings, gemini_api_key="test-only-key")
    mock_gemini(monkeypatch, None, None, status=429)
    with pytest.raises(MediaError, match="limiti"):
        await gemini_quiz.generate_pdf_quiz([SOURCE], settings, Mock(), AsyncMock())


@pytest.mark.asyncio
async def test_gemini_retries_server_overload_once(settings, monkeypatch):
    calls = mock_gemini(
        monkeypatch,
        {"questions": [ITEM]},
        {"checks": [{"id": 1, "correct": 1}]},
        first_unavailable=True,
    )
    result = await gemini_quiz.generate_pdf_quiz(
        [SOURCE], replace(settings, gemini_api_key="test-only-key"), Mock(), AsyncMock()
    )
    assert len(result) == 1 and calls[0] == calls[1]
    assert len(calls) == 3 and "items" in calls[2]


@pytest.mark.asyncio
async def test_gemini_pdf_flows_through_bot_and_uses_existing_quiz_menu(
    tmp_path, settings, monkeypatch
):
    settings = replace(settings, gemini_api_key="test-only-key")
    calls = mock_gemini(monkeypatch, {"questions": [ITEM]}, {"checks": [{"id": 1, "correct": 1}]})
    store = BotStore(tmp_path / "store.sqlite", 77)
    handlers = Handlers(ConversionService(settings), settings, False, store)
    pdf_path = tmp_path / "input.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_textbox(fitz.Rect(40, 40, 530, 480), SOURCE)
        pdf.save(pdf_path)

    async def download(bot, file_id, destination):
        destination.write_bytes(pdf_path.read_bytes())

    monkeypatch.setattr(handlers.features, "download", download)
    message = SimpleNamespace(text=AI_QUIZ, reply_text=AsyncMock(), document=None)
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=77),
        effective_chat=SimpleNamespace(id=77, type="private"),
    )
    status = SimpleNamespace(edit_text=AsyncMock())
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=status)))
    await handlers.receive(update, context)
    assert "Google" in message.reply_text.call_args.args[0]
    message.text = None
    message.document = SimpleNamespace(
        file_id="file",
        file_name="input.pdf",
        file_size=500,
        mime_type="application/pdf",
    )
    await handlers.receive(update, context)
    await asyncio.gather(*handlers.features.tasks.values())
    result = status.edit_text.call_args
    assert "Gemini" in result.args[0]
    buttons = result.kwargs["reply_markup"].inline_keyboard
    quiz_id = buttons[0][0].callback_data.split(":")[-1]
    assert [b.text for b in buttons[1]] == ["📝 Word test", "📄 PDF test"]
    assert store.quiz(77, quiz_id)["questions"][0]["correct"] == 1
    assert len(calls) == 2 and "pages" in calls[0]
    with pytest.raises(MediaError, match="bir daqiqa"):
        store.reserve_gemini_job(77, 10)
