import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from telegram import User
from telegram.ext import Application

from botapp.handlers import Handlers
from botapp.media import MediaError
from botapp.quiz import export_quiz, parse_questions, read_quiz_docx, read_quiz_pdf
from botapp.service import ConversionService
from botapp.store import BotStore
from botapp.ui import ADMIN, QUIZ

SOURCE = """1. O'zbekiston poytaxti?
A) Toshkent B) Samarqand
Javob: A
2. Ikki karra uch?
A) Besh
B) Olti
Javob: B
"""


def make_store(tmp_path):
    store = BotStore(tmp_path / "bot.sqlite3", 11)
    for user_id in (11, 22, 33):
        store.touch(User(user_id, f"User {user_id}", False, username=f"user{user_id}"), user_id)
    return store


def test_roles_owner_only_persist_and_revoke(tmp_path):
    store = make_store(tmp_path)
    assert store.is_admin(11) and not store.is_admin(22)
    store.change_admin(11, 22, add=True)
    assert BotStore(store.path, 11).is_admin(22)
    with pytest.raises(MediaError):
        store.change_admin(22, 33, add=True)
    with pytest.raises(MediaError):
        store.change_admin(11, 11, add=False)
    store.change_admin(11, 22, add=False)
    assert not store.is_admin(22)
    assert store.resolve_user("@USER22") == 22


def test_tracking_deduplicates_and_backup_restores_atomically(tmp_path):
    store = make_store(tmp_path)
    store.touch(User(22, "Changed", False), 22)
    assert store.statistics()["updates"] == 3
    store.touch(User(22, "Changed", False), 100)
    store.change_admin(11, 22, add=True)
    backup = store.export_metadata(11)
    target = BotStore(tmp_path / "restored.sqlite3", 11)
    assert target.restore_metadata(11, backup) == 3
    assert target.statistics()["updates"] == 4 and target.is_admin(22)
    target.restore_metadata(11, backup)
    assert target.statistics()["updates"] == 4
    broken = json.loads(backup)
    broken["users"][1]["id"] = -99
    with pytest.raises(MediaError):
        target.restore_metadata(11, json.dumps(broken).encode())
    assert target.statistics()["total"] == 3
    for action in (target.export_metadata, lambda actor: target.restore_metadata(actor, backup)):
        with pytest.raises(MediaError):
            action(22)


@pytest.mark.parametrize(
    "body",
    [
        SOURCE,
        SOURCE.replace("Javob: A\n", "").replace("Javob: B\n", "") + "Javoblar: 1-A, 2-B",
        SOURCE.replace("A) Toshkent", "*A) Toshkent").replace("Javob: A\n", ""),
    ],
)
def test_explicit_answer_formats(body):
    questions = parse_questions(body)
    assert [q["correct"] for q in questions] == [0, 1]
    assert questions[0]["options"] == ["Toshkent", "Samarqand"]


def test_plain_qa_uses_only_source_answers():
    questions = parse_questions("Savol: 2+2?\nJavob: 4\nSavol: 3+3?\nJavob: 6")
    assert len(questions) == 2
    assert [q["options"][q["correct"]] for q in questions] == ["4", "6"]
    assert all(set(q["options"]) == {"4", "6"} for q in questions)


def test_inline_answer_key_heading():
    body = SOURCE.replace("Javob: A\n", "").replace("Javob: B\n", "")
    assert [q["correct"] for q in parse_questions(body + "Javoblar kaliti: 1-A, 2-B")] == [0, 1]


async def test_immediate_cancel_before_task_started_releases_busy(tmp_path, settings):
    handlers = Handlers(ConversionService(settings), settings, True, make_store(tmp_path))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await handlers.features.launch(update_for(22), context, "parse", "f")
    task = handlers.features.tasks[22]
    handlers.features.cancel(22)
    await asyncio.gather(task, return_exceptions=True)
    assert not handlers.features.tasks and 22 not in handlers.service.busy


@pytest.mark.parametrize(
    "body",
    [
        SOURCE.replace("Javob: A", ""),
        SOURCE + "Javoblar: 1-B, 2-B",
        SOURCE.replace("B) Samarqand", "B) Toshkent"),
        SOURCE + SOURCE,
        "Savol: Yagona savol?\nJavob: Yagona javob",
        "1. Savol?\nA) Ha\nB) Yo'q\nJavob: C",
    ],
)
def test_missing_conflicting_or_ambiguous_answers_rejected(body):
    with pytest.raises(MediaError):
        parse_questions(body)


def test_quiz_score_single_submission_isolation_restart(tmp_path):
    store = make_store(tmp_path)
    quiz_id = store.save_quiz(22, parse_questions(SOURCE), False)
    store.start_attempt(22, quiz_id)
    assert store.answer(22, quiz_id, 0, 0)["score"] == 1
    with pytest.raises(MediaError):
        store.answer(22, quiz_id, 0, 0)
    with pytest.raises(MediaError):
        store.start_attempt(33, quiz_id)
    store = BotStore(store.path, 11)
    assert store.start_attempt(22, quiz_id)["phase"] == "feedback"
    store.next_question(22, quiz_id, 0)
    store.answer(22, quiz_id, 1, 0)
    result = store.next_question(22, quiz_id, 1)
    assert result["phase"] == "done" and result["score"] == 1


def test_native_pdf_read_and_editable_exports(tmp_path, settings):
    path = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), SOURCE, fontsize=11)
        pdf.save(path)
    result = read_quiz_pdf(path, settings)
    assert not result["scanned"] and len(result["questions"]) == 2
    export_quiz(result["questions"], tmp_path / "out.docx", "word")
    paragraphs = [p.text for p in Document(tmp_path / "out.docx").paragraphs]
    assert "Javoblar kaliti" in paragraphs
    assert any("Toshkent" in text for text in paragraphs)
    export_quiz(result["questions"], tmp_path / "out.pdf", "pdf")
    with fitz.open(tmp_path / "out.pdf") as pdf:
        assert len(pdf) == 2
        assert "Javoblar kaliti" not in pdf[0].get_text()
        assert "Javoblar kaliti" in pdf[1].get_text()
        assert all(not page.get_images() for page in pdf)


def update_for(user_id, text=None, callback=None, document=None):
    return SimpleNamespace(
        effective_user=User(user_id, "Tester", False),
        effective_chat=SimpleNamespace(id=user_id, type="private"),
        effective_message=SimpleNamespace(text=text, reply_text=AsyncMock(), document=document),
        callback_query=callback,
        update_id=user_id,
    )


async def test_revoked_admin_stale_callback_is_denied(tmp_path, settings):
    store = make_store(tmp_path)
    store.change_admin(11, 22, add=True)
    handlers = Handlers(ConversionService(settings), settings, True, store)
    assert ADMIN in [b.text for row in handlers.keyboard(22).keyboard for b in row]
    store.change_admin(11, 22, add=False)
    query = SimpleNamespace(data="admin:users:0", answer=AsyncMock(), edit_message_text=AsyncMock())
    await handlers.features.admin_callback(update_for(22, callback=query), SimpleNamespace())
    query.edit_message_text.assert_not_awaited()
    assert query.answer.call_args.kwargs["show_alert"]
    assert ADMIN not in [b.text for row in handlers.keyboard(22).keyboard for b in row]


async def test_registered_quiz_routes_parse_and_score(tmp_path, settings, monkeypatch):
    store = make_store(tmp_path)
    service = ConversionService(settings)
    handlers = Handlers(service, settings, True, store)
    app = Application.builder().token(settings.token).build()
    handlers.register(app)
    callbacks = [h for h in app.handlers[0] if getattr(h, "pattern", None)]
    assert any(h.pattern.pattern == "^quiz:" for h in callbacks)
    status = SimpleNamespace(edit_text=AsyncMock())
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=status)))
    await handlers.receive(update_for(22, QUIZ), context)
    assert service.sessions[22].mode == "quiz"

    async def download(bot, file_id, output):
        output.write_bytes(b"test")

    monkeypatch.setattr(handlers.features, "download", download)
    monkeypatch.setattr(
        "botapp.features.read_quiz_pdf",
        lambda *args: {
            "questions": parse_questions(SOURCE),
            "scanned": False,
        },
    )
    doc = SimpleNamespace(
        mime_type="application/pdf", file_name="test.pdf", file_size=4, file_id="f"
    )
    await handlers.receive(update_for(22, document=doc), context)
    await asyncio.gather(*handlers.features.tasks.values())
    assert 22 not in service.busy
    markup = status.edit_text.call_args.kwargs["reply_markup"]
    quiz_id = markup.inline_keyboard[0][0].callback_data.split(":")[-1]
    query = SimpleNamespace(
        data=f"quiz:start:{quiz_id}", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update = update_for(22, callback=query)
    await handlers.features.quiz_callback(update, context)
    assert "Savol 1 / 2" in query.edit_message_text.call_args.args[0]
    query.data = f"quiz:answer:{quiz_id}:0:0"
    await handlers.features.quiz_callback(update, context)
    assert store.attempt(22, quiz_id)["score"] == 1


async def test_cancel_quiz_releases_busy_and_preserves_files_until_thread_finishes(
    tmp_path, settings
):
    handlers = Handlers(ConversionService(settings), settings, True, make_store(tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(*args):
        entered.set()
        await release.wait()

    handlers.features.download = download
    status = SimpleNamespace(edit_text=AsyncMock())
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=status)))
    await handlers.features.launch(update_for(22), context, "parse", "f")
    task = handlers.features.tasks[22]
    await entered.wait()
    handlers.features.cancel(22)
    await asyncio.gather(task, return_exceptions=True)
    assert 22 not in handlers.service.busy and not handlers.features.tasks


def test_word_quiz_preserves_numbered_answer_paragraphs_and_references(tmp_path):
    source = tmp_path / "sorov.docx"
    doc = Document()
    doc.add_paragraph("ЖАВОБЛАР")
    first = doc.add_paragraph().add_run("1. Биринчи савол?")
    first.font.highlight_color = WD_COLOR_INDEX.YELLOW
    doc.add_paragraph("Ж: Биринчи жавоб.")
    doc.add_paragraph("2. Жавоб ичидаги рўйхат.")
    doc.add_paragraph("Иккинчи хатбоши.")
    doc.add_paragraph("2. Иккинчи савол?")
    doc.add_paragraph("Ж: 1-савол билан бир хил.")
    doc.add_paragraph("2. Учинчи савол?")
    doc.add_paragraph("Жавоб: " + "Узун жавоб. " * 650)
    doc.save(source)

    questions = read_quiz_docx(source)["questions"]
    assert len(questions) == 3
    assert questions[0]["model_answer"].endswith("рўйхат.\nИккинчи хатбоши.")
    assert questions[1]["model_answer"].endswith(questions[0]["model_answer"])
    assert len(questions[2]["model_answer"]) > 7000
    for kind, suffix in (("word", "docx"), ("pdf", "pdf")):
        output = tmp_path / f"test.{suffix}"
        export_quiz(questions, output, kind)
        if kind == "word":
            text = "\n".join(p.text for p in Document(output).paragraphs)
        else:
            with fitz.open(output) as pdf:
                text = "\n".join(page.get_text() for page in pdf)
        sheet, key = text.split("Javoblar kaliti", 1)
        assert "Иккинчи савол?" in sheet and "Биринчи жавоб." not in sheet
        assert "Иккинчи хатбоши." in key
        assert key.count("Узун жавоб.") == 650


async def test_word_batch_creates_scored_multiple_choice_tests(tmp_path, settings, monkeypatch):
    store = make_store(tmp_path)
    service = ConversionService(settings)
    handlers = Handlers(service, settings, True, store)
    source = tmp_path / "questions.docx"
    doc = Document()
    doc.add_paragraph("1. Савол?")
    answer = (
        "Машқни бажариш учун ўқувчилар керакли маълумотларни дафтарга ёзадилар. Жавоб матни. " * 100
    )
    doc.add_paragraph("Ж: " + answer)
    doc.add_paragraph("2. Кейинги савол?")
    doc.add_paragraph("Ж: Гуруҳ аъзолари машғулот вақтида янги маълумотларни диққат билан ўқийди.")
    doc.save(source)
    release = asyncio.Event()

    async def download(bot, file_id, output):
        await release.wait()
        output.write_bytes(source.read_bytes())

    monkeypatch.setattr(handlers.features, "download", download)
    statuses = [SimpleNamespace(edit_text=AsyncMock()) for _ in range(4)]
    context = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=statuses), send_document=AsyncMock())
    )
    await handlers.receive(update_for(22, "🧩 PDFdan test"), context)
    for num in range(4):
        document = SimpleNamespace(
            mime_type="application/octet-stream",
            file_name=f"{num}.docx",
            file_size=source.stat().st_size,
            file_id=str(num),
        )
        await handlers.receive(update_for(22, document=document), context)
    release.set()
    await asyncio.gather(*handlers.features.tasks.values())
    ids = set()
    for num, status in enumerate(statuses):
        assert f"{num}.docx" in status.edit_text.call_args.args[0]
        markup = status.edit_text.call_args.kwargs["reply_markup"]
        ids.add(markup.inline_keyboard[0][0].callback_data.split(":")[-1])
    assert len(ids) == 4
    quiz_id = ids.pop()
    query = SimpleNamespace(
        data=f"quiz:start:{quiz_id}", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update = update_for(22, callback=query)
    await handlers.features.quiz_callback(update, context)
    assert "____" in query.edit_message_text.call_args.args[0]
    keyboard = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert [button.text for button in keyboard[0]] == list("ABCD")
    assert len(query.edit_message_text.call_args.args[0]) < 4096
    questions = store.quiz(22, quiz_id)["questions"]
    correct = questions[0]["correct"]
    assert questions[0]["options"][correct] in answer
    query.data = f"quiz:answer:{quiz_id}:0:{correct}"
    await handlers.features.quiz_callback(update, context)
    assert "✅ To'g'ri!" in query.edit_message_text.call_args.args[0]
    with pytest.raises(MediaError):
        store.answer(22, quiz_id, 0, correct)
    query.data = f"quiz:full:{quiz_id}:0"
    await handlers.features.quiz_callback(update, context)
    assert (
        context.bot.send_document.call_args.kwargs["document"].getvalue().decode() == answer.strip()
    )
    query.data = f"quiz:next:{quiz_id}:0"
    await handlers.features.quiz_callback(update, context)
    wrong = (questions[1]["correct"] + 1) % 4
    query.data = f"quiz:answer:{quiz_id}:1:{wrong}"
    await handlers.features.quiz_callback(update, context)
    assert "Noto'g'ri" in query.edit_message_text.call_args.args[0]
    query.data = f"quiz:next:{quiz_id}:1"
    await handlers.features.quiz_callback(update, context)
    assert "1 / 2" in query.edit_message_text.call_args.args[0]
    assert store.attempt(22, quiz_id)["phase"] == "done"
