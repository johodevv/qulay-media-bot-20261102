"""Convert office files with LibreOffice in a fresh, non-interactive profile."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree
from pypdf import PdfReader

from botapp.config import MB, Settings
from botapp.media import MediaError
from botapp.pdf_word import pdf_to_word

OFFICE_MAX_BYTES = 30 * MB
OFFICE_MAX_PAGES = 50
_office_lock = asyncio.Lock()
_profile_directory: tempfile.TemporaryDirectory | None = None


def office_profile() -> Path:
    global _profile_directory
    if _profile_directory is None:
        _profile_directory = tempfile.TemporaryDirectory(prefix="qb-lo-profile-")
        root = Path(_profile_directory.name)
        user = root / "user"
        user.mkdir()
        (user / "registrymodifications.xcu").write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Common/History">'
            '<prop oor:name="PickListSize" oor:op="fuse"><value>0</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Common/Save/Document">'
            '<prop oor:name="AutoSave" oor:op="fuse"><value>false</value></prop>'
            '<prop oor:name="CreateBackup" oor:op="fuse"><value>false</value></prop></item>'
            "</oor:items>",
            encoding="utf-8",
        )
    return Path(_profile_directory.name)


@asynccontextmanager
async def office_directory():
    # A short root also avoids Windows MAX_PATH failures in LibreOffice's profile.
    parent = Path(tempfile.gettempdir())
    directory = tempfile.TemporaryDirectory(prefix="qb-office-", dir=parent)
    path = Path(directory.name)
    resolved_path = await asyncio.to_thread(path.resolve)
    resolved_parent = await asyncio.to_thread(parent.resolve)
    if not resolved_path.is_relative_to(resolved_parent):
        raise MediaError("Vaqtinchalik katalog noto'g'ri.")
    try:
        yield path
    finally:
        # Windows LibreOffice can finish closing its profile just after the CLI exits.
        for attempt in range(20):
            try:
                await asyncio.to_thread(directory.cleanup)
                break
            except OSError:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.2)


def validate_office(path: Path, kind: str) -> bool:
    """Return whether a PDF includes pages without text; reject unsupported inputs."""
    if path.stat().st_size > OFFICE_MAX_BYTES:
        raise MediaError("Word/PDF uchun fayl 30 MB dan oshmasin. Video limiti alohida.")
    if kind == "docx":
        try:
            with ZipFile(path) as archive:
                items = archive.infolist()
                if len(items) > 5000 or sum(item.file_size for item in items) > 100 * MB:
                    raise MediaError("Word fayli juda katta yoki murakkab.")
                names = {item.filename for item in items}
                if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                    raise MediaError("Haqiqiy .docx Word faylini yuboring.")
                parser = etree.XMLParser(resolve_entities=False, no_network=True)
                for item in items:
                    name = item.filename.lower()
                    if "vbaproject" in name or "/embeddings/" in name:
                        raise MediaError(
                            "Makrosiz va ichiga boshqa fayl qo'shilmagan Word yuboring."
                        )
                    if name.endswith(".rels"):
                        if item.file_size > MB:
                            raise MediaError("Word fayli juda murakkab.")
                        root = etree.fromstring(archive.read(item), parser)
                        for rel in root:
                            if rel.get("TargetMode", "").lower() == "external" and not rel.get(
                                "Type", ""
                            ).endswith("/hyperlink"):
                                raise MediaError(
                                    "Word ichidagi tashqi rasm yoki havolali obyektni "
                                    "olib tashlang."
                                )
        except (BadZipFile, etree.XMLSyntaxError, KeyError, OSError) as exc:
            raise MediaError("Word faylini ochib bo'lmadi. .docx formatida qayta saqlang.") from exc
        return False
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise MediaError("Parolsiz PDF faylini yuboring.")
        if not 1 <= len(reader.pages) <= OFFICE_MAX_PAGES:
            raise MediaError(f"PDF {OFFICE_MAX_PAGES} sahifadan oshmasin.")
        return any(not page.extract_text().strip() for page in reader.pages)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("PDF faylini ochib bo'lmadi. Boshqa nusxasini yuboring.") from exc


async def convert_office(source: Path, output: Path, kind: str, settings: Settings) -> bool:
    async with _office_lock:
        return await _convert_office(source, output, kind, settings)


async def _convert_office(source: Path, output: Path, kind: str, settings: Settings) -> bool:
    if not settings.soffice:
        raise MediaError("Word ↔ PDF xizmati serverda hali tayyor emas.")
    if kind not in {"docx", "pdf"}:
        raise MediaError("Word (.docx) yoki PDF faylini yuboring.")
    scanned = await asyncio.to_thread(validate_office, source, kind)
    if kind == "pdf":
        task = asyncio.create_task(asyncio.to_thread(pdf_to_word, source, output))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        return scanned
    async with office_directory() as work:
        # Reuse only the application profile, never inputs/outputs, to avoid repeating
        # LibreOffice's first-run setup. The lock permits one office process at a time.
        profile = await asyncio.to_thread(office_profile)
        args = [
            settings.soffice,
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
        ]
        target = "pdf:writer_pdf_Export" if kind == "docx" else "docx:Office Open XML Text"
        source_path = await asyncio.to_thread(source.resolve)
        args.extend(["--convert-to", target, "--outdir", str(work), str(source_path)])
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=min(settings.job_timeout, 180))
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await killer.wait()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise MediaError("Hujjat juda uzoq ishlanyapti. Kichikroq fayl yuboring.") from exc
        produced = work / (source.stem + (".pdf" if kind == "docx" else ".docx"))
        if process.returncode or not produced.is_file() or not produced.stat().st_size:
            raise MediaError("Hujjat aylantirilmadi. Faylni qayta saqlab yuboring.")
        await asyncio.to_thread(shutil.move, str(produced), str(output))
    return scanned
