"""OCR into visible, editable text while retaining non-text artwork and positions."""

from __future__ import annotations

import io
import statistics
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytesseract
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from botapp.config import Settings
from botapp.media import MediaError, normalized_image, ocr_available

VML = "urn:schemas-microsoft-com:vml"


@dataclass(frozen=True)
class TextBox:
    text: str
    x: int
    y: int
    width: int
    height: int
    color: tuple[int, int, int]
    bold: bool = False
    serif: bool = False


@dataclass
class LayoutPage:
    background: Image.Image
    boxes: list[TextBox]
    scale: float

    @property
    def width(self) -> float:
        return self.background.width * self.scale

    @property
    def height(self) -> float:
        return self.background.height * self.scale


@lru_cache(maxsize=4)
def font_path(bold: bool = False, serif: bool = False) -> Path:
    if serif:
        filename = "timesbd.ttf" if bold else "times.ttf"
        linux = "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"
        for path in (
            Path("C:/Windows/Fonts") / filename,
            Path("/usr/share/fonts/truetype/dejavu") / linux,
        ):
            if path.is_file():
                return path
    candidates = (
        [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
        if bold
        else [
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    raise MediaError("Hujjat shrifti o'rnatilmagan. Serverda DejaVu Sans kerak.")


def ink_and_background(image: Image.Image, box: tuple[int, int, int, int]):
    x, y, width, height = box
    # Sample the outer border, not the glyphs themselves; preserve colored paper.
    border = []
    for xx in range(max(0, x - 2), min(image.width, x + width + 2)):
        for yy in (max(0, y - 2), min(image.height - 1, y + height + 1)):
            border.append(image.getpixel((xx, yy)))
    for yy in range(y, min(image.height, y + height)):
        for xx in (max(0, x - 2), min(image.width - 1, x + width + 1)):
            border.append(image.getpixel((xx, yy)))
    background = tuple(int(statistics.median(pixel[i] for pixel in border)) for i in range(3))
    with image.crop((x, y, x + width, y + height)) as crop:
        with Image.new("RGB", crop.size, background) as paper:
            difference = ImageChops.difference(crop, paper).convert("L")
        peak = difference.getextrema()[1]
        if peak == 0:
            difference.close()
            return background, background
        mask = difference.point(lambda value: 255 if value >= max(1, peak * 0.7) else 0)
        foreground = tuple(int(value) for value in ImageStat.Stat(crop, mask).median)
        difference.close()
        mask.close()
    return foreground, background


def orient_document(image: Image.Image, settings: Settings) -> Image.Image:
    """OSD uses glyph shapes, not EXIF (phone photos often lack rotation tags)."""
    try:
        orientation = pytesseract.image_to_osd(
            image, output_type=pytesseract.Output.DICT, timeout=min(settings.job_timeout, 20)
        )
    except (RuntimeError, pytesseract.TesseractError):
        # Sparse pages and artwork do not supply enough glyphs for orientation.
        return image
    angle = int(orientation.get("rotate", 0))
    if angle not in {90, 180, 270} or float(orientation.get("orientation_conf", 0)) < 2.5:
        return image
    rotated = image.rotate(-angle, expand=True, fillcolor="white")
    image.close()
    return rotated


def clean_paper(image: Image.Image) -> Image.Image:
    """Flatten lighting on neutral paper; retain colored diagrams and seals."""
    sample = image.copy()
    sample.thumbnail((64, 64))
    pixels = list(sample.get_flattened_data())
    sample.close()
    paper = [p for p in pixels if max(p) - min(p) < 30 and min(p) > 130]
    if len(paper) < len(pixels) * 0.72:
        return image
    gray = image.convert("L")
    small = gray.resize((max(1, image.width // 20), max(1, image.height // 20)))
    light = small.filter(ImageFilter.MaxFilter(7))
    blurred = light.filter(ImageFilter.GaussianBlur(2))
    light.close()
    illumination = blurred.resize(image.size, Image.Resampling.BILINEAR)
    gray.close()
    small.close()
    blurred.close()
    paper_rgb = Image.merge("RGB", (illumination, illumination, illumination))
    corrected = ImageChops.subtract(image, paper_rgb, offset=255)
    neutral = ImageChops.subtract(image.convert("L"), illumination, offset=255)
    contrast = ImageOps.autocontrast(neutral, cutoff=1)
    neutral.close()
    white = contrast.point([min(255, round(value * 255 / 215)) for value in range(256)])
    contrast.close()
    channels = image.split()
    chroma = ImageChops.difference(
        ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]),
        ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2]),
    )
    mask = chroma.point(lambda value: 255 if value > 40 else 0)
    cleaned = Image.composite(corrected, white.convert("RGB"), mask)
    for channel in channels:
        channel.close()
    chroma.close()
    mask.close()
    white.close()
    illumination.close()
    paper_rgb.close()
    corrected.close()
    image.close()
    return cleaned


def image_layout(path: Path, settings: Settings) -> LayoutPage:
    if not ocr_available(settings):
        raise MediaError("OCR va tillar serverda sozlanmagan.")
    image = normalized_image(path, settings.max_image_pixels)
    image.thumbnail((1800, 2600), Image.Resampling.LANCZOS)
    image = orient_document(image, settings)
    prepared = clean_paper(image)
    scanned_paper = prepared is not image
    image = prepared
    # Small phone screenshots benefit from larger glyphs, within the same pixel cap.
    factor = min(
        1800 / image.width, 2600 / image.height, 2 if min(image.width, image.height) < 900 else 1
    )
    if factor > 1.01:
        enlarged = image.resize(
            (round(image.width * factor), round(image.height * factor)), Image.Resampling.LANCZOS
        )
        image.close()
        image = enlarged
    gray = ImageOps.autocontrast(image.convert("L"))
    try:
        data = pytesseract.image_to_data(
            gray,
            lang=settings.ocr_languages,
            config="--psm 3",
            output_type=pytesseract.Output.DICT,
            timeout=min(settings.job_timeout, 90),
        )
    except (RuntimeError, pytesseract.TesseractError) as exc:
        image.close()
        raise MediaError("Yozuvni tanish amalga oshmadi. Tiniqroq hujjat yuboring.") from exc
    finally:
        gray.close()
    words = []
    for index, text in enumerate(data["text"]):
        text = "".join(char for char in text.strip() if ord(char) >= 32)
        confidence = 80
        if not text or float(data["conf"][index]) < confidence or data["height"][index] < 6:
            continue
        words.append((index, text))
    if not words:
        image.close()
        raise MediaError("Tahrirlanadigan matn topilmadi. Rasm ko'rinishidagi formatni tanlang.")
    if len(words) > 5000:
        image.close()
        raise MediaError("Sahifada matn juda ko'p. Alohida qismlarga bo'lib yuboring.")
    median_height = statistics.median(data["height"][index] for index, _ in words)
    background = image.copy()
    draw = ImageDraw.Draw(background)
    boxes = []
    for index, text in words:
        x, y, width, height = (
            int(data[name][index]) for name in ("left", "top", "width", "height")
        )
        original_box = (x, y, width, height)
        if scanned_paper and height > median_height * 1.6:
            # OCR can combine a text row with a table rule: never stretch that into huge text.
            continue
        color, fill = ink_and_background(image, (x, y, width, height))
        # Colored seals and artwork remain intact rather than becoming OCR fragments.
        if max(color) - min(color) > 40:
            continue
        with image.crop((x, y, x + width, y + height)) as crop:
            solid = Image.new("RGB", crop.size, fill)
            difference = ImageChops.difference(crop, solid).convert("L")
            mask = difference.point(lambda value: 255 if value > 35 else 0)
            bounds = mask.getbbox()
            solid.close()
            difference.close()
            mask.close()
        if bounds:
            left, top, right, bottom = bounds
            x, y = x + left, y + top
            width, height = right - left, bottom - top
        candidate = TextBox(
            text, x, y, width, height, color, height > median_height * 2, scanned_paper
        )
        _, _, stretch = text_metrics(candidate, 1)
        if not 0.6 <= stretch <= 1.65:
            # Icon fragments and implausible OCR geometry keep their original pixels.
            continue
        # Low-confidence decorations remain original artwork. Do not invent missing words.
        draw.rectangle(
            (
                max(0, original_box[0] - 1),
                max(0, original_box[1] - 1),
                min(image.width - 1, original_box[0] + original_box[2]),
                min(image.height - 1, original_box[1] + original_box[3]),
            ),
            fill=fill,
        )
        boxes.append(candidate)
    image.close()
    if not boxes:
        background.close()
        raise MediaError("Tahrirlanadigan matn topilmadi. Rasm ko'rinishidagi formatni tanlang.")
    # A few isolated characters on a sideways or unreadable photo are not success.
    recognized_chars = sum(len(box.text) for box in boxes)
    if recognized_chars < 12 or len(boxes) < len(words) * 0.5:
        background.close()
        raise MediaError("Matn yetarlicha tanilmadi. Hujjatni tik va tiniq suratga olib yuboring.")
    scale = min(0.75, 595 / background.width, 1500 / background.height)
    return LayoutPage(background, boxes, scale)


@lru_cache(maxsize=4)
def reference_font(bold: bool, serif: bool = False):
    return ImageFont.truetype(str(font_path(bold, serif)), 100)


def text_metrics(box: TextBox, scale: float):
    font = reference_font(box.bold, box.serif)
    left, top, right, bottom = font.getbbox(box.text, anchor="ls")
    fontsize = box.height * scale * 100 / max(1, bottom - top)
    baseline = box.y * scale - top / 100 * fontsize
    natural_width = font.getlength(box.text) / 100 * fontsize
    horizontal = box.width * scale / max(0.1, natural_width)
    return fontsize, baseline, horizontal


def images_to_editable_pdf(paths: list[Path], output: Path, settings: Settings) -> None:
    for serif in (False, True):
        for bold in (False, True):
            name = pdf_font_name(bold, serif)
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(font_path(bold, serif))))
    document = Canvas(str(output), pageCompression=1)
    for path in paths:
        layout = image_layout(path, settings)
        try:
            document.setPageSize((layout.width, layout.height))
            document.drawImage(
                ImageReader(layout.background), 0, 0, width=layout.width, height=layout.height
            )
            for box in layout.boxes:
                size, baseline, _ = text_metrics(box, layout.scale)
                name = pdf_font_name(box.bold, box.serif)
                text = document.beginText(box.x * layout.scale, layout.height - baseline)
                text.setFont(name, size)
                text.setFillColorRGB(*(c / 255 for c in box.color))
                width = pdfmetrics.stringWidth(box.text, name, size)
                text.setHorizScale(box.width * layout.scale / max(0.1, width) * 100)
                text.textOut(box.text)
                document.drawText(text)
            document.showPage()
        finally:
            layout.background.close()
    document.save()


def pdf_font_name(bold: bool, serif: bool) -> str:
    return f"Editable{'Serif' if serif else 'Sans'}{'Bold' if bold else 'Regular'}"


def add_background(paragraph, layout: LayoutPage):
    buffer = io.BytesIO()
    layout.background.save(buffer, format="PNG")
    buffer.seek(0)
    inline = (
        paragraph.add_run()
        .add_picture(buffer, width=Pt(layout.width), height=Pt(layout.height))
        ._inline
    )
    anchor = OxmlElement("wp:anchor")
    for key, value in {
        "distT": "0",
        "distB": "0",
        "distL": "0",
        "distR": "0",
        "simplePos": "0",
        "relativeHeight": "0",
        "behindDoc": "1",
        "locked": "0",
        "layoutInCell": "1",
        "allowOverlap": "1",
    }.items():
        anchor.set(key, value)
    simple = OxmlElement("wp:simplePos")
    simple.set("x", "0")
    simple.set("y", "0")
    anchor.append(simple)
    for axis in ("H", "V"):
        position = OxmlElement(f"wp:position{axis}")
        position.set("relativeFrom", "page")
        offset = OxmlElement("wp:posOffset")
        offset.text = "0"
        position.append(offset)
        anchor.append(position)
    anchor.append(deepcopy(inline.find(qn("wp:extent"))))
    anchor.append(OxmlElement("wp:wrapNone"))
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        child = inline.find(qn(tag))
        if child is not None:
            anchor.append(deepcopy(child))
    inline.getparent().replace(inline, anchor)


def add_textbox(paragraph, box: TextBox, scale: float, index: int):
    size, _, horizontal = text_metrics(box, scale)
    # Text frames are editable Word text, not text painted into an image.
    shape = etree.Element(f"{{{VML}}}rect", nsmap={"v": VML})
    shape.set("id", f"EditableText{index}")
    shape.set("filled", "f")
    shape.set("stroked", "f")
    shape.set(
        "style",
        f"position:absolute;margin-left:{box.x * scale:.3f}pt;"
        f"margin-top:{box.y * scale - size * 0.13:.3f}pt;"
        f"width:{max(box.width * scale + 4, 10):.3f}pt;height:{size * 1.8:.3f}pt;"
        "mso-position-horizontal-relative:page;mso-position-vertical-relative:page;"
        "v-text-anchor:top;z-index:10",
    )
    textbox = etree.SubElement(shape, f"{{{VML}}}textbox")
    textbox.set("inset", "0,0,0,0")
    content = OxmlElement("w:txbxContent")
    p = OxmlElement("w:p")
    properties = OxmlElement("w:pPr")
    spacing = OxmlElement("w:spacing")
    for key, value in {
        "before": "0",
        "after": "0",
        "line": str(round(size * 23)),
        "lineRule": "exact",
    }.items():
        spacing.set(qn(f"w:{key}"), value)
    properties.append(spacing)
    p.append(properties)
    run, rpr = OxmlElement("w:r"), OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for key in ("ascii", "hAnsi", "cs", "eastAsia"):
        fonts.set(qn(f"w:{key}"), "Times New Roman" if box.serif else "Arial")
    rpr.append(fonts)
    for tag, value in (
        ("sz", round(size * 2)),
        ("szCs", round(size * 2)),
        ("w", max(30, min(250, round(horizontal * 100)))),
    ):
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:val"), str(value))
        rpr.append(node)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "".join(f"{c:02X}" for c in box.color))
    rpr.append(color)
    if box.bold:
        rpr.append(OxmlElement("w:b"))
    run.append(rpr)
    text = OxmlElement("w:t")
    text.text = box.text
    run.append(text)
    p.append(run)
    content.append(p)
    textbox.append(content)
    pict = OxmlElement("w:pict")
    pict.append(shape)
    paragraph.add_run()._r.append(pict)


def images_to_layout_word(paths: list[Path], output: Path, settings: Settings) -> None:
    layouts = (image_layout(path, settings) for path in paths)
    layouts_to_word(layouts, output)


def layouts_to_word(layouts, output: Path) -> None:
    document = Document()
    index = 0
    for page_number, layout in enumerate(layouts):
        try:
            section = (
                document.add_section(WD_SECTION_START.NEW_PAGE)
                if page_number
                else document.sections[0]
            )
            section.page_width, section.page_height = Pt(layout.width), Pt(layout.height)
            section.top_margin = section.bottom_margin = Pt(0)
            section.left_margin = section.right_margin = Pt(0)
            section.header_distance = section.footer_distance = Pt(0)
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(1)
            add_background(paragraph, layout)
            for box in layout.boxes:
                add_textbox(paragraph, box, layout.scale, index)
                index += 1
        finally:
            layout.background.close()
    document.save(output)
