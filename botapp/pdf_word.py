"""Export existing PDF text to editable positioned Word text without OCR."""

from pathlib import Path

import pymupdf as fitz
from PIL import Image

from botapp.document_layout import LayoutPage, TextBox, layouts_to_word, reference_font
from botapp.media import MediaError


def pdf_to_word(source: Path, output: Path) -> None:
    def pages():
        with fitz.open(source) as document:
            for page in document:
                if page.rotation:
                    page.remove_rotation()
                factor = min(2, 1800 / page.rect.width, 2600 / page.rect.height)
                boxes = []
                for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
                    for line in block.get("lines", []):
                        # Keep rotated/vertical glyphs as artwork rather than distort them.
                        if tuple(line.get("dir", (1, 0))) != (1, 0):
                            continue
                        for span in line["spans"]:
                            text = "".join(c for c in span["text"] if ord(c) >= 32)
                            if not text.strip():
                                continue
                            if len(boxes) >= 5000:
                                raise MediaError("PDF sahifasi juda murakkab.")
                            bold = bool(span["flags"] & 16)
                            font = reference_font(bold)
                            _, top, _, bottom = font.getbbox(text, anchor="ls")
                            size = span["size"] * factor
                            origin = span["origin"]
                            rect = fitz.Rect(span["bbox"])
                            color = span["color"]
                            boxes.append(
                                TextBox(
                                    text,
                                    round(origin[0] * factor),
                                    round(origin[1] * factor + top / 100 * size),
                                    max(1, round(rect.width * factor)),
                                    max(1, round((bottom - top) / 100 * size)),
                                    ((color >> 16) & 255, (color >> 8) & 255, color & 255),
                                    bold,
                                )
                            )
                            page.add_redact_annot(rect, fill=False, cross_out=False)
                if boxes:
                    # Preserve photos, fills, seals and table lines while removing native text.
                    page.apply_redactions(images=0, graphics=0, text=0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(factor, factor), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                yield LayoutPage(image, boxes, 1 / factor)

    layouts_to_word(pages(), output)
