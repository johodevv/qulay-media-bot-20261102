import io
from dataclasses import replace
from pathlib import Path

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from botapp.media import (
    MediaError,
    images_to_pdf,
    images_to_text_word,
    images_to_word,
    normalized_image,
    ocr_available,
)


@pytest.fixture
def images(tmp_path):
    paths = []
    for name, color in [("one", "red"), ("two", "blue")]:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (600, 400), color).save(path)
        paths.append(path)
    return paths


def test_pdf_has_one_a4_page_per_image_in_order(images, tmp_path):
    output = tmp_path / "result.pdf"
    images_to_pdf(images, output, 1_000_000)
    reader = PdfReader(output)
    assert len(reader.pages) == 2
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595.2, abs=1)
    colors = []
    for page in reader.pages:
        image = Image.open(io.BytesIO(page.images[0].data))
        colors.append(image.getpixel((image.width // 2, image.height // 2)))
    assert colors[0][0] > 200 and colors[0][2] < 50
    assert colors[1][2] > 200 and colors[1][0] < 50


def test_word_embeds_images_and_page_break(images, tmp_path):
    output = tmp_path / "result.docx"
    images_to_word(images, output, 1_000_000)
    document = Document(output)
    assert len(document.inline_shapes) == 2
    assert 'w:type="page"' in document.element.xml
    assert all(shape.width < document.sections[0].page_width for shape in document.inline_shapes)


def test_transparency_becomes_white_and_exif_orientation_is_applied(tmp_path):
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (100, 50), (0, 0, 0, 0)).save(transparent)
    with normalized_image(transparent, 10000) as result:
        assert result.getpixel((0, 0)) == (255, 255, 255)
    rotated = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 50), "red")
    exif = image.getexif()
    exif[274] = 6
    image.save(rotated, exif=exif)
    with normalized_image(rotated, 10000) as result:
        assert result.size == (50, 100)


def test_invalid_and_oversized_images_are_rejected(images, tmp_path):
    with pytest.raises(MediaError, match="juda katta"):
        normalized_image(images[0], 100)
    invalid = tmp_path / "fake.jpg"
    invalid.write_text("not an image", encoding="utf-8")
    with pytest.raises(MediaError, match="o'qib bo'lmadi"):
        normalized_image(invalid, 1_000_000)


def test_real_ocr_produces_editable_word(settings, tmp_path):
    if not ocr_available(settings):
        pytest.skip("Tesseract and configured languages are not installed")
    image = Image.new("RGB", (1500, 400), "white")
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    font_path = next((path for path in candidates if path.exists()), None)
    if not font_path:
        pytest.skip("OCR test needs Arial or DejaVu Sans font")
    font = ImageFont.truetype(str(font_path), 65)
    ImageDraw.Draw(image).text((70, 100), "HUJJAT 12345", font=font, fill="black")
    source, output = tmp_path / "text.png", tmp_path / "text.docx"
    image.save(source)
    images_to_text_word([source], output, replace(settings, ocr_languages="eng"))
    text = "\n".join(paragraph.text for paragraph in Document(output).paragraphs)
    assert "12345" in text
    assert "HUJJAT" in text
    assert len(Document(output).inline_shapes) == 0
