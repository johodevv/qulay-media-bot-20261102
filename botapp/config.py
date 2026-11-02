from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MB = 1024 * 1024


def positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} musbat son bo'lishi kerak.")
    return value


def executable(env: str, name: str) -> str | None:
    configured = os.getenv(env, "").strip()
    if configured:
        return shutil.which(configured)
    detected = shutil.which(name)
    if detected:
        return detected
    if name == "tesseract":
        local = ROOT / "tools" / "tesseract" / "tesseract.exe"
        if local.is_file():
            return str(local)
        for root in (os.getenv("ProgramFiles", ""), os.getenv("LOCALAPPDATA", "")):
            for folder in ("Tesseract-OCR", "Programs/Tesseract-OCR"):
                candidate = Path(root) / folder / "tesseract.exe"
                if candidate.is_file():
                    return str(candidate)
    if name == "soffice":
        candidate = ROOT / "tools" / "libreoffice" / "program" / "soffice.com"
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)
    mode: str = "polling"
    port: int = 8080
    webhook_url: str = ""
    webhook_secret: str = field(default="", repr=False)
    allowed_user_ids: frozenset[int] = frozenset()
    api_url: str = ""
    local_mode: bool = False
    embedded_api: bool = False
    api_id: int = field(default=0, repr=False)
    api_hash: str = field(default="", repr=False)
    api_dir: Path = Path("/tmp/telegram-bot-api")
    max_input_bytes: int = 20 * MB
    max_output_bytes: int = 49 * MB
    max_video_seconds: int = 600
    job_timeout: int = 1200
    max_queue: int = 5
    max_images: int = 10
    max_image_pixels: int = 25_000_000
    session_ttl: int = 1800
    max_sessions: int = 200
    ffmpeg_threads: int = 1
    ffmpeg: str | None = None
    ffprobe: str | None = None
    tesseract: str | None = None
    soffice: str | None = None
    ocr_languages: str = "uzb+uzb_cyrl+rus+eng"

    @classmethod
    def from_env(cls, require_token: bool = True) -> Settings:
        load_dotenv(ROOT / ".env")
        if not os.getenv("TESSDATA_PREFIX", "").strip():
            os.environ.pop("TESSDATA_PREFIX", None)
        settings = cls(
            token=os.getenv("BOT_TOKEN", "").strip(),
            mode=os.getenv("BOT_MODE", "polling").strip(),
            port=positive_int("PORT", 8080),
            webhook_url=(
                os.getenv("WEBHOOK_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "")
            ).rstrip("/"),
            webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
            allowed_user_ids=frozenset(
                int(item.strip())
                for item in os.getenv("ALLOWED_USER_IDS", "").split(",")
                if item.strip()
            ),
            api_url=os.getenv("TELEGRAM_API_URL", "").rstrip("/"),
            local_mode=os.getenv("TELEGRAM_LOCAL_MODE", "false").lower() == "true",
            embedded_api=os.getenv("TELEGRAM_EMBEDDED_API", "false").lower() == "true",
            api_id=int(os.getenv("TELEGRAM_API_ID", "") or "0"),
            api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
            api_dir=Path(os.getenv("TELEGRAM_API_DIR", "/tmp/telegram-bot-api")),
            max_input_bytes=positive_int("MAX_INPUT_MB", 20) * MB,
            max_output_bytes=positive_int("MAX_OUTPUT_MB", 49) * MB,
            max_video_seconds=positive_int("MAX_VIDEO_SECONDS", 600),
            job_timeout=positive_int("JOB_TIMEOUT_SECONDS", 1200),
            max_queue=positive_int("MAX_QUEUE_SIZE", 5),
            max_images=positive_int("MAX_IMAGES", 10),
            max_image_pixels=positive_int("MAX_IMAGE_PIXELS", 25_000_000),
            session_ttl=positive_int("SESSION_TTL_SECONDS", 1800),
            max_sessions=positive_int("MAX_SESSIONS", 200),
            ffmpeg_threads=positive_int("FFMPEG_THREADS", 1),
            ffmpeg=executable("FFMPEG_PATH", "ffmpeg"),
            ffprobe=executable("FFPROBE_PATH", "ffprobe"),
            tesseract=executable("TESSERACT_PATH", "tesseract"),
            soffice=executable("SOFFICE_PATH", "soffice"),
            ocr_languages=os.getenv("OCR_LANGUAGES", "uzb+uzb_cyrl+rus+eng"),
        )
        settings.validate(require_token)
        return settings

    def validate(self, require_token: bool = True) -> None:
        if require_token and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", self.token):
            raise ValueError(".env faylidagi BOT_TOKEN ga BotFather tokenini yozing.")
        if self.mode not in {"polling", "webhook"}:
            raise ValueError("BOT_MODE faqat polling yoki webhook bo'ladi.")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT 1 va 65535 orasida bo'lishi kerak.")
        if self.api_url:
            parsed = urlparse(self.api_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("TELEGRAM_API_URL noto'g'ri.")
        elif self.max_input_bytes > 20 * MB or self.max_output_bytes > 49 * MB:
            raise ValueError("20/49 MB dan katta limit uchun TELEGRAM_API_URL kerak.")
        if self.local_mode and not self.api_url:
            raise ValueError("TELEGRAM_LOCAL_MODE uchun TELEGRAM_API_URL kerak.")
        if (
            self.max_input_bytes > 20 * MB or self.max_output_bytes > 49 * MB
        ) and not self.local_mode:
            raise ValueError(
                "Katta fayllarni xotiraga yuklamaslik uchun TELEGRAM_LOCAL_MODE kerak."
            )
        if self.max_input_bytes > 1900 * MB:
            raise ValueError("Kirish limiti 1900 MB dan oshmasin.")
        if self.max_output_bytes > 1900 * MB:
            raise ValueError("Chiqish limiti 1900 MB dan oshmasin.")
        if self.embedded_api:
            if not self.local_mode or self.api_url != "http://127.0.0.1:8081":
                raise ValueError("Ichki Bot API uchun local mode va 127.0.0.1:8081 kerak.")
            if self.api_id <= 0 or not re.fullmatch(r"[a-fA-F0-9]{32}", self.api_hash):
                raise ValueError(
                    "TELEGRAM_API_ID va TELEGRAM_API_HASH ni my.telegram.org dan oling."
                )
        if self.mode == "webhook":
            parsed = urlparse(self.webhook_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Webhook rejimida WEBHOOK_URL ommaviy HTTPS manzil bo'lsin.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", self.webhook_secret):
                raise ValueError("WEBHOOK_SECRET 24–256 ta harf, raqam, _ yoki - bo'lsin.")
        if not re.fullmatch(r"[a-z_]+(?:\+[a-z_]+)*", self.ocr_languages):
            raise ValueError("OCR_LANGUAGES noto'g'ri.")
