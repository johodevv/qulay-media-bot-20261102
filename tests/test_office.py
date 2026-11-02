from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from docx import Document
from pypdf import PdfReader, PdfWriter

from botapp.handlers import Handlers
from botapp.media import MediaError
from botapp.office import convert_office, validate_office
from botapp.service import ConversionService, Upload
from botapp.ui import WORD_PDF


async def test_real_office_roundtrip_keeps_text_table_and_pages(settings, tmp_path):
    if not settings.soffice:
        pytest.skip("LibreOffice required")
    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_heading("Hujjat 12345", 0)
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Sana", "02.11.2026"
    table.cell(1, 0).text, table.cell(1, 1).text = "Hisob", "123456789"
    doc.add_page_break()
    doc.add_paragraph("IKKINCHI SAHIFA 98765")
    doc.save(source)
    pdf, word = tmp_path / "result.pdf", tmp_path / "roundtrip.docx"
    assert not await convert_office(source, pdf, "docx", settings)
    reader = PdfReader(pdf)
    assert len(reader.pages) == 2
    assert "123456789" in reader.pages[0].extract_text()
    assert "98765" in reader.pages[1].extract_text()
    assert not await convert_office(pdf, word, "pdf", settings)
    with ZipFile(word) as archive:
        xml = archive.read("word/document.xml").decode()
    assert all(text in xml for text in ("12345", "123456789", "98765"))
    assert not list(tmp_path.glob("office-*"))


async def test_colored_pdf_table_labels_remain_visible_after_word_roundtrip(settings, tmp_path):
    if not settings.soffice:
        pytest.skip("LibreOffice required")
    source, word, final = tmp_path / "report.pdf", tmp_path / "word.docx", tmp_path / "final.pdf"
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    table = Table([["Label", "Example"], ["Name", "Test Value"]], colWidths=[125, 300])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]
        )
    )
    SimpleDocTemplate(str(source)).build([table])
    await convert_office(source, word, "pdf", settings)
    await convert_office(word, final, "docx", settings)
    text = " ".join(page.extract_text() for page in PdfReader(final).pages)
    for expected in ["Label", "Example", "Name", "Test Value"]:
        assert expected in text


def test_bad_word_external_objects_and_encrypted_pdf_rejected(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a docx")
    with pytest.raises(MediaError, match="ochib bo'lmadi"):
        validate_office(bad, "docx")
    with ZipFile(bad, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships><Relationship TargetMode="External" '
            'Type="image" Target="https://example.com/image"/></Relationships>',
        )
    with pytest.raises(MediaError, match="tashqi"):
        validate_office(bad, "docx")
    encrypted = tmp_path / "private.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("password")
    writer.write(encrypted)
    with pytest.raises(MediaError, match="Parolsiz"):
        validate_office(encrypted, "pdf")


def test_pdf_scan_and_page_limits_are_explicit(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    assert validate_office(source, "pdf")
    for _ in range(50):
        writer.add_blank_page(width=595, height=842)
    writer.write(source)
    with pytest.raises(MediaError, match="50 sahifa"):
        validate_office(source, "pdf")


async def test_word_upload_enqueues_automatically_and_wrong_direction_is_rejected(settings):
    settings = replace(settings, soffice="soffice")
    service = ConversionService(settings)
    handlers = Handlers(service, settings, True)
    status = SimpleNamespace(message_id=12, edit_text=AsyncMock())
    message = SimpleNamespace(
        text=WORD_PDF,
        photo=None,
        video=None,
        document=None,
        reply_text=AsyncMock(return_value=status),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
        callback_query=None,
    )
    await handlers.receive(update, SimpleNamespace())
    message.text = None
    message.document = SimpleNamespace(
        file_id="a", file_size=100, file_name="source.pdf", mime_type="application/pdf"
    )
    await handlers.receive(update, SimpleNamespace())
    assert service.queue.empty()
    message.document.file_name = "source.docx"
    message.document.mime_type = "application/octet-stream"
    await handlers.receive(update, SimpleNamespace())
    job = service.queue.get_nowait()
    assert job.action == "docx-pdf" and job.uploads[0].kind == "docx"
    assert job.status_id == 12 and 1 in service.busy


def test_document_actions_cannot_be_used_for_other_file_types(settings):
    service = ConversionService(settings)
    session = service.add_upload(1, Upload("a", 10, "pdf"))
    with pytest.raises(MediaError, match="mos emas"):
        service.enqueue(1, 1, session.nonce, "docx-pdf", 1)
    service.enqueue(1, 1, session.nonce, "pdf-docx", 1)
    assert service.queue.get_nowait().action == "pdf-docx"
