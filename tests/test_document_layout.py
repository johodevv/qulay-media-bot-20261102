from dataclasses import replace

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from botapp.document_layout import (
    clean_paper,
    font_path,
    images_to_editable_pdf,
    images_to_layout_word,
)
from botapp.media import images_to_text_word, ocr_available


@pytest.fixture
def layout_source(settings, tmp_path):
    if not ocr_available(settings):
        pytest.skip("Tesseract and configured languages are required")
    image = Image.new("RGB", (1000, 1200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path()), 36)
    draw.text((70, 120), "HUJJAT 12345", font=font, fill="black")
    draw.text((70, 300), "Sana", font=font, fill="black")
    draw.text((550, 300), "02.11.2026", font=font, fill="black")
    draw.text((70, 410), "Hisob", font=font, fill="black")
    draw.text((550, 410), "123456", font=font, fill="black")
    draw.ellipse((650, 750, 850, 950), outline="blue", width=8)
    source = tmp_path / "synthetic.png"
    image.save(source)
    image.close()
    return source, replace(settings, ocr_languages="eng")


def test_pdf_contains_visible_text_at_original_column_positions(layout_source, tmp_path):
    source, settings = layout_source
    output = tmp_path / "layout.pdf"
    images_to_editable_pdf([source], output, settings)
    page = PdfReader(output).pages[0]
    text = page.extract_text()
    assert all(value in text for value in ("HUJJAT", "12345", "Sana", "02.11.2026", "123456"))
    assert float(page.mediabox.width) / float(page.mediabox.height) == pytest.approx(1000 / 1200)
    positions = {}

    def visit(text, cm, tm, font, size):
        if text.strip():
            positions[text.strip()] = tm[:]

    page.extract_text(visitor_text=visit)
    assert positions["02.11.2026"][4] > positions["Sana"][4] + 200
    assert positions["02.11.2026"][5] == pytest.approx(positions["Sana"][5], abs=5)
    # Visible PDF text rather than the hidden render-mode-3 OCR overlay.
    assert b"3 Tr" not in page.get_contents().get_data()
    assert len(page.images) == 1


def test_word_text_is_editable_and_pages_retain_original_aspect(layout_source, tmp_path):
    source, settings = layout_source
    output = tmp_path / "layout.docx"
    images_to_layout_word([source, source], output, settings)
    document = Document(output)
    boxes = document.element.xpath("//w:txbxContent")
    text = " ".join(
        node.text for box in boxes for node in box.xpath(".//w:t", namespaces=box.nsmap)
    )
    assert "HUJJAT" in text and "123456" in text
    assert len(document.sections) == 2
    for section in document.sections:
        assert section.page_width / section.page_height == pytest.approx(1000 / 1200, abs=0.001)
    assert len(document.element.xpath("//wp:anchor")) == 2
    assert "mso-position-horizontal-relative:page" in document.element.xml


def test_no_ocr_text_fails_clearly(settings, tmp_path):
    if not ocr_available(settings):
        pytest.skip("Tesseract is required")
    source = tmp_path / "blank.png"
    Image.new("RGB", (300, 300), "white").save(source)
    from botapp.media import MediaError

    with pytest.raises(MediaError, match="matn topilmadi"):
        images_to_editable_pdf([source], tmp_path / "empty.pdf", settings)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_sideways_photographed_document_becomes_upright_editable(settings, tmp_path, angle):
    if not ocr_available(settings):
        pytest.skip("Tesseract is required")
    source = tmp_path / "photographed.png"
    image = Image.new("RGB", (1000, 1400), (190, 190, 190))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path()), 30)
    for row in range(24):
        draw.text(
            (50, 30 + row * 54), "Document biology example number 12345", font=font, fill="black"
        )
    image.rotate(angle, expand=True).save(source)
    image.close()
    output = tmp_path / "upright.pdf"
    images_to_editable_pdf([source], output, replace(settings, ocr_languages="eng"))
    page = PdfReader(output).pages[0]
    assert page.mediabox.height > page.mediabox.width
    # Repeated aligned numbers can be segmented by Tesseract as a separate column.
    assert page.extract_text().count("Document") >= 20
    assert page.extract_text().count("biology") >= 20
    background = page.images[0].image.convert("RGB")
    assert min(background.getpixel((5, 5))) > 245
    background.close()
    if angle == 90:
        word = tmp_path / "editable-body.docx"
        images_to_text_word([source], word, replace(settings, ocr_languages="eng"))
        document = Document(word)
        assert " ".join(p.text for p in document.paragraphs).count("Document") >= 20
        assert not document.inline_shapes


def test_paper_cleanup_preserves_colored_artwork():
    image = Image.new("RGB", (400, 500), (185, 185, 185))
    ImageDraw.Draw(image).ellipse((250, 350, 350, 450), outline=(0, 0, 180), width=6)
    cleaned = clean_paper(image)
    assert cleaned.getpixel((10, 10)) == (255, 255, 255)
    red, green, blue = cleaned.getpixel((300, 350))
    assert blue > red + 70 and blue > green + 70
    cleaned.close()
