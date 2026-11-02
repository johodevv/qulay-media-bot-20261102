from __future__ import annotations

import asyncio
import io
import json
import math
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pytesseract
from docx import Document
from docx.shared import Cm, Pt
from PIL import Image, ImageOps, UnidentifiedImageError

from botapp.config import Settings

RESOLUTIONS = {
    144: "144p",
    360: "360p",
    480: "480p",
    720: "720p",
    1080: "1080p",
    1440: "1440p (QHD)",
    2160: "4K (2160p)",
}
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
VIDEO_FORMATS = "mov,matroska,webm,avi,mpeg,mpegts,flv,ogg"


class MediaError(Exception):
    """Foydalanuvchiga ko'rsatilishi mumkin bo'lgan xato."""


def normalized_image(path: Path, max_pixels: int) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as original:
                if original.format not in IMAGE_FORMATS:
                    raise MediaError("JPG, PNG, WEBP, BMP yoki TIFF rasm yuboring.")
                if original.width * original.height > max_pixels:
                    raise MediaError("Rasm o'lchami juda katta. Kichikroq nusxasini yuboring.")
                oriented = ImageOps.exif_transpose(original)
                rgba = oriented.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgba.close()
                if oriented is not original:
                    oriented.close()
                return background
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise MediaError("Rasmni o'qib bo'lmadi. Boshqa JPG yoki PNG fayl yuboring.") from exc


def images_to_pdf(paths: list[Path], output: Path, max_pixels: int) -> None:
    # Bir vaqtda faqat bir asl surat ochiladi; A4 sahifalar 150 DPI bilan cheklanadi.
    pages = []
    try:
        for path in paths:
            with normalized_image(path, max_pixels) as image:
                image.thumbnail((1140, 1654), Image.Resampling.LANCZOS)
                page = Image.new("RGB", (1240, 1754), "white")
                page.paste(image, ((1240 - image.width) // 2, (1754 - image.height) // 2))
                pages.append(page)
        if not pages:
            raise MediaError("Kamida bitta rasm kerak.")
        pages[0].save(
            output, "PDF", save_all=True, append_images=pages[1:], resolution=150, quality=90
        )
    finally:
        for page in pages:
            page.close()


def new_document():
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(1.5)
    section.left_margin = section.right_margin = Cm(1.5)
    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(12)
    return document


def images_to_word(paths: list[Path], output: Path, max_pixels: int) -> None:
    document = new_document()
    for index, path in enumerate(paths):
        if index:
            document.add_page_break()
        with normalized_image(path, max_pixels) as image:
            image.thumbnail((2400, 3400), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            buffer.seek(0)
            width_cm = min(18, 25 * image.width / image.height)
            document.add_picture(buffer, width=Cm(width_cm))
    document.save(output)


def ocr_available(settings: Settings) -> bool:
    if not settings.tesseract:
        return False
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract
    try:
        installed = set(pytesseract.get_languages(config=""))
        return set(settings.ocr_languages.split("+")).issubset(installed)
    except (pytesseract.TesseractError, OSError):
        return False


def images_to_text_word(paths: list[Path], output: Path, settings: Settings) -> None:
    if not ocr_available(settings):
        raise MediaError("Matnni tanish uchun OCR dasturi va til modellari o'rnatilmagan.")
    document = new_document()
    recognized = False
    for index, path in enumerate(paths):
        if index:
            document.add_page_break()
        with normalized_image(path, settings.max_image_pixels) as image:
            image.thumbnail((2500, 3500), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image.convert("L"))
            try:
                text = pytesseract.image_to_string(
                    image,
                    lang=settings.ocr_languages,
                    config="--psm 3",
                    timeout=min(settings.job_timeout, 90),
                ).strip()
            except (RuntimeError, pytesseract.TesseractError) as exc:
                raise MediaError(
                    "Matnni o'qib bo'lmadi. Tiniqroq surat bilan qayta urinib ko'ring."
                ) from exc
            finally:
                image.close()
        # OCR natijasidagi XML uchun yaroqsiz boshqaruv belgilarini olib tashlash.
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        if text:
            recognized = True
            for paragraph in text.split("\n\n"):
                document.add_paragraph(paragraph)
        else:
            document.add_paragraph("Bu sahifada matn aniqlanmadi.")
    if not recognized:
        raise MediaError("Rasmlarda matn topilmadi. Tiniqroq rasm yoki Word (rasm) ni tanlang.")
    document.save(output)


async def run_process(
    args: list[str], seconds: float, on_progress: Callable[[float], None] | None = None
) -> bytes:
    # Shell ishlatilmaydi. Serverdagi yoki internetdagi manzillarni media sifatida ochmaymiz.
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def consume():
        if on_progress is None:
            stdout, _stderr = await process.communicate()
            return stdout

        async def read_progress():
            async for raw in process.stdout:
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith("out_time_us="):
                    try:
                        on_progress(max(0, int(line.split("=", 1)[1])) / 1_000_000)
                    except ValueError:
                        pass

        await asyncio.gather(read_progress(), process.stderr.read())
        await process.wait()
        return b""

    try:
        async with asyncio.timeout(seconds):
            stdout = await consume()
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise MediaError("Videoni o'qish yoki o'zgartirish amalga oshmadi. Boshqa fayl yuboring.")
    return stdout


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool
    fps: float = 30


async def probe_video(path: Path, settings: Settings) -> VideoInfo:
    if not settings.ffprobe:
        raise MediaError("Video xizmati uchun FFprobe o'rnatilishi kerak.")
    data = await run_process(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            VIDEO_FORMATS,
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        30,
    )
    try:
        metadata = json.loads(data)
        streams = metadata.get("streams", [])
        video = next(
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic")
        )
        width, height = int(video["width"]), int(video["height"])
        ratio = video.get("sample_aspect_ratio", "1:1").split(":")
        if len(ratio) == 2 and int(ratio[0]) > 0 and int(ratio[1]) > 0:
            width = round(width * int(ratio[0]) / int(ratio[1]))
        rotation = float(video.get("tags", {}).get("rotate", 0))
        for side in video.get("side_data_list", []):
            rotation = float(side.get("rotation", rotation))
        if round(rotation) % 180 == 90:
            width, height = height, width
        duration = float(metadata.get("format", {}).get("duration") or video.get("duration"))
        if min(width, height) < 2 or not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise MediaError("Video davomiyligi yoki o'lchamini aniqlab bo'lmadi.") from exc
    if width * height > 40_000_000:
        raise MediaError("Video o'lchami juda katta. 4K yoki kichikroq video yuboring.")
    if duration > settings.max_video_seconds:
        raise MediaError(f"Video {settings.max_video_seconds // 60} daqiqadan uzun bo'lmasin.")
    try:
        fps = float(Fraction(video.get("avg_frame_rate") or "30"))
        if not 0 < fps <= 240:
            fps = 30
    except (ValueError, ZeroDivisionError):
        fps = 30
    return VideoInfo(
        width, height, duration, any(stream.get("codec_type") == "audio" for stream in streams), fps
    )


def target_dimensions(info: VideoInfo, resolution: int) -> tuple[int, int]:
    if resolution == 0:
        return max(2, info.width // 2 * 2), max(2, info.height // 2 * 2)
    if resolution not in RESOLUTIONS:
        raise MediaError("Video o'lchami noto'g'ri tanlangan.")
    short, long = resolution, math.ceil(resolution * 16 / 9 / 2) * 2
    box_w, box_h = (short, long) if info.height > info.width else (long, short)
    scale = min(1.0, box_w / info.width, box_h / info.height)
    result = (
        max(2, round(info.width * scale / 2) * 2),
        max(2, round(info.height * scale / 2) * 2),
    )
    if result == (info.width, info.height):
        raise MediaError("Bu o'lcham videongizdan kichik emas. Pastroq o'lchamni tanlang.")
    return result


async def convert_video(
    source: Path,
    output: Path,
    resolution: int,
    settings: Settings,
    on_progress: Callable[[float], None] | None = None,
) -> VideoInfo:
    if not settings.ffmpeg:
        raise MediaError("Video xizmati uchun FFmpeg o'rnatilishi kerak.")
    info = await probe_video(source, settings)
    width, height = target_dimensions(info, resolution)
    # Fayl limitidan kichikroq maqsad; oxirida haqiqiy hajm ham tekshiriladi.
    input_bytes = (await asyncio.to_thread(source.stat)).st_size
    total_bitrate = int(min(settings.max_output_bytes, input_bytes) * 8 * 0.85 / info.duration)
    audio_bitrate = min(96000, max(24000, total_bitrate // 4)) if info.has_audio else 0
    video_bitrate = max(16000, total_bitrate - audio_bitrate)
    filters = f"scale={width}:{height}:flags=fast_bilinear,setsar=1,fps={min(info.fps, 30):.6f}"
    args = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        VIDEO_FORMATS,
        "-threads",
        str(settings.ffmpeg_threads),
        "-i",
        str(source),
        "-map",
        "0:V:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-sn",
        "-dn",
        "-vf",
        filters,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-maxrate",
        str(video_bitrate),
        "-bufsize",
        str(video_bitrate * 2),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        str(settings.ffmpeg_threads),
        "-filter_threads",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        str(audio_bitrate or 96000),
        "-movflags",
        "+faststart",
        "-t",
        str(settings.max_video_seconds + 1),
        "-fs",
        str(settings.max_output_bytes + 1),
        str(output),
    ]
    try:
        await run_process(
            args,
            settings.job_timeout,
            (lambda seconds: on_progress(min(0.99, seconds / info.duration)))
            if on_progress
            else None,
        )
    except TimeoutError as exc:
        raise MediaError(
            "Video uchun vaqt tugadi. Qisqaroq video yoki kichikroq o'lcham tanlang."
        ) from exc
    try:
        output_size = (await asyncio.to_thread(output.stat)).st_size
    except FileNotFoundError as exc:
        raise MediaError("Video natijasi yaratilmagan. Boshqa fayl bilan urinib ko'ring.") from exc
    if output_size > settings.max_output_bytes:
        raise MediaError(
            "Natija fayl limitidan katta. Qisqaroq video yoki pastroq o'lcham tanlang."
        )
    if output_size >= input_bytes:
        raise MediaError(
            "Video allaqachon kuchli siqilgan. Tanlangan usulda hajmi kichraymadi. "
            "Pastroq o'lcham bilan qayta urinib ko'ring."
        )
    result = await probe_video(output, settings)
    if abs(result.duration - info.duration) > max(1.5, info.duration * 0.02):
        raise MediaError("Video to'liq chiqmagan. Qisqaroq video yoki pastroq o'lcham tanlang.")
    return result
