"""Exercise installed LibreOffice filters inside the deployment image."""

import asyncio
import tempfile
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader

from botapp.config import Settings
from botapp.office import convert_office


async def main():
    settings = Settings.from_env(require_token=False)
    with tempfile.TemporaryDirectory(prefix="office-smoke-") as folder:
        root = Path(folder)
        source, pdf, word = root / "source.docx", root / "output.pdf", root / "output.docx"
        doc = Document()
        doc.add_paragraph("CONVERSION CHECK 12345")
        doc.save(source)
        await convert_office(source, pdf, "docx", settings)
        assert "12345" in PdfReader(pdf).pages[0].extract_text()
        await convert_office(pdf, word, "pdf", settings)
        with ZipFile(word) as archive:
            assert b"12345" in archive.read("word/document.xml")
    print("Office smoke: DOCX -> PDF -> DOCX passed")


if __name__ == "__main__":
    asyncio.run(main())
