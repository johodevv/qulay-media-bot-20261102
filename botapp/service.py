from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from botapp.config import MB, Settings
from botapp.media import (
    RESOLUTIONS,
    MediaError,
    convert_video,
    images_to_pdf,
    images_to_text_word,
    images_to_word,
)
from botapp.ui import progress_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Upload:
    file_id: str
    size: int
    kind: str
    width: int = 0
    height: int = 0


@dataclass
class Session:
    nonce: str = field(default_factory=lambda: secrets.token_hex(5))
    uploads: list[Upload] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    mode: str = ""


@dataclass
class Progress:
    stage: str = "Fayl Telegram'dan olinmoqda…"
    percent: int | None = None
    started: float = field(default_factory=time.monotonic)

    def text(self) -> str:
        return progress_text(self.stage, self.percent, time.monotonic() - self.started)


@dataclass(frozen=True)
class Job:
    user_id: int
    chat_id: int
    uploads: tuple[Upload, ...]
    action: str
    status_id: int


class ConversionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[int, Session] = {}
        self.busy: set[int] = set()
        self.cancelled: set[int] = set()
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.max_queue)
        self.video_queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.max_queue)
        self.worker: asyncio.Task | None = None
        self.video_worker: asyncio.Task | None = None
        self.progress: dict[int, Progress] = {}
        self.api_files: dict[int, list[Path]] = {}

    def select_service(self, user_id: int, mode: str) -> None:
        if user_id in self.busy:
            raise MediaError("Avvalgi topshiriq tugashini kuting yoki bekor qiling.")
        self.prune()
        if user_id not in self.sessions and len(self.sessions) >= self.settings.max_sessions:
            raise MediaError("Bot band. Birozdan so'ng qayta urinib ko'ring.")
        self.sessions[user_id] = Session(mode=mode)

    def status_text(self, user_id: int) -> str:
        self.prune()
        if progress := self.progress.get(user_id):
            return progress.text()
        if user_id in self.busy:
            return "⏳ Topshiriq navbatda. Boshlanganda jarayon ko'rsatiladi."
        if session := self.sessions.get(user_id):
            if session.uploads:
                return f"{len(session.uploads)} ta fayl qabul qilindi. Tayyorlash tugmasini bosing."
            return "Xizmat tanlangan. Kerakli rasm yoki videoni yuboring."
        return "Hozir topshiriq yo'q. Pastdagi tugmalardan xizmat tanlang."

    def prune(self) -> None:
        now = time.monotonic()
        for user_id in list(self.sessions):
            if now - self.sessions[user_id].touched > self.settings.session_ttl:
                del self.sessions[user_id]

    def add_upload(self, user_id: int, upload: Upload) -> Session:
        self.prune()
        if user_id in self.busy:
            raise MediaError("Avvalgi topshiriq tugashini kuting. Holat: /status")
        if upload.size <= 0 or upload.size > self.settings.max_input_bytes:
            raise MediaError(f"Bitta fayl {self.settings.max_input_bytes // MB} MB dan oshmasin.")
        session = self.sessions.get(user_id)
        if (
            not session
            or upload.kind == "video"
            or (session.uploads and session.uploads[0].kind != upload.kind)
        ):
            if not session and len(self.sessions) >= self.settings.max_sessions:
                raise MediaError("Bot band. Birozdan so'ng qayta urinib ko'ring.")
            session = Session(mode=session.mode if session else "")
            self.sessions[user_id] = session
        if len(session.uploads) >= self.settings.max_images:
            raise MediaError(
                f"Bir hujjatga ko'pi bilan {self.settings.max_images} ta rasm qo'shing."
            )
        if sum(item.size for item in session.uploads) + upload.size > self.settings.max_input_bytes:
            raise MediaError(
                "Bir topshiriqdagi jami fayllar "
                f"{self.settings.max_input_bytes // MB} MB dan oshmasin."
            )
        session.uploads.append(upload)
        session.touched = time.monotonic()
        return session

    def get_session(self, user_id: int, nonce: str) -> Session:
        self.prune()
        session = self.sessions.get(user_id)
        if not session or not session.uploads or session.nonce != nonce:
            raise MediaError("Bu tugma eskirgan. Rasm yoki videoni qayta yuboring.")
        return session

    def enqueue(self, user_id: int, chat_id: int, nonce: str, action: str, status_id: int) -> int:
        if user_id in self.busy:
            raise MediaError("Topshiriq allaqachon navbatda. Holat: /status")
        session = self.get_session(user_id, nonce)
        if session.uploads[0].kind == "video":
            if not action.startswith("video-") or not action[6:].isdigit():
                raise MediaError("Video o'lchamini tanlang.")
            resolution = int(action[6:])
            if resolution not in RESOLUTIONS and resolution != 0:
                raise MediaError("Video o'lchami noto'g'ri.")
            upload = session.uploads[0]
            if resolution and upload.width and upload.height:
                if resolution >= min(upload.width, upload.height):
                    raise MediaError("Pastroq o'lchamni yoki «Hajmni kamaytirish»ni tanlang.")
        elif action not in {"pdf", "word", "text"}:
            raise MediaError("Rasm uchun PDF yoki Word tanlang.")
        job = Job(user_id, chat_id, tuple(session.uploads), action, status_id)
        queue = self.video_queue if action.startswith("video-") else self.queue
        if self.queue.qsize() + self.video_queue.qsize() >= self.settings.max_queue:
            raise MediaError("Navbat to'ldi. Birozdan so'ng shu tugmani qayta bosing.")
        try:
            queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            raise MediaError("Navbat to'ldi. Birozdan so'ng shu tugmani qayta bosing.") from exc
        self.busy.add(user_id)
        self.sessions.pop(user_id)
        return queue.qsize()

    def cancel(self, user_id: int) -> str:
        self.sessions.pop(user_id, None)
        if user_id in self.busy:
            self.cancelled.add(user_id)
            return "Bekor qilish belgilandi. Jarayon tugatilgach natija yuborilmaydi."
        return "Tanlov va rasmlar tozalandi. Yangi rasm yoki video yuboring."

    async def start(self, bot: Bot) -> None:
        self.worker = asyncio.create_task(self.work(bot, self.queue), name="document-worker")
        self.video_worker = asyncio.create_task(
            self.work(bot, self.video_queue), name="video-worker"
        )

    async def stop(self) -> None:
        # Navbatdagilar bajarilmaydi; joriy ishga fayllarini xavfsiz yopish uchun vaqt beriladi.
        for user_id in self.busy:
            self.cancelled.add(user_id)
        tasks = [task for task in (self.worker, self.video_worker) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def work(self, bot: Bot, queue: asyncio.Queue[Job]) -> None:
        while True:
            job = await queue.get()
            started = time.monotonic()
            try:
                if job.user_id not in self.cancelled:
                    await self.execute(bot, job)
            except asyncio.CancelledError:
                raise
            except MediaError as exc:
                await self.notify(bot, job, str(exc))
            except TelegramError:
                logger.warning("Telegram orqali topshiriqni yetkazib bo'lmadi.")
                await self.notify(bot, job, "Faylni olish/yuborishda xato. Qayta urinib ko'ring.")
            except Exception as exc:
                # Fayl nomlari, OCR matni va tokenli URL'larni logga chiqarmaymiz.
                logger.error("Topshiriq xatosi: %s", type(exc).__name__)
                await self.notify(bot, job, "Kutilmagan xato. Boshqa fayl bilan urinib ko'ring.")
            finally:
                logger.info(
                    "Topshiriq yakuni: %s; vaqt=%.1fs", job.action, time.monotonic() - started
                )
                self.progress.pop(job.user_id, None)
                self.busy.discard(job.user_id)
                self.cancelled.discard(job.user_id)
                queue.task_done()

    async def notify(self, bot: Bot, job: Job, text: str) -> None:
        try:
            await bot.edit_message_text(
                chat_id=job.chat_id,
                message_id=job.status_id,
                text=text,
                read_timeout=8,
                write_timeout=8,
            )
        except TelegramError:
            logger.info("Holat xabarini yangilab bo'lmadi.")

    async def execute(self, bot: Bot, job: Job) -> None:
        progress = Progress()
        self.progress[job.user_id] = progress
        ticker = asyncio.create_task(self.tick(bot, job, progress))
        try:
            await self.process_files(bot, job, progress)
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            for path in self.api_files.pop(job.user_id, []):
                # Faqat ishonchli ichki API qaytargan media fayli; katalog/DB o'chirilmaydi.
                if path.is_relative_to(self.settings.api_dir.resolve()) and path.is_file():
                    await asyncio.to_thread(path.unlink, missing_ok=True)

    async def tick(self, bot: Bot, job: Job, progress: Progress) -> None:
        while True:
            await self.notify(bot, job, progress.text())
            await asyncio.sleep(4)

    async def process_files(self, bot: Bot, job: Job, progress: Progress) -> None:
        with tempfile.TemporaryDirectory(prefix="qulaybot-") as folder:
            root = Path(folder)
            paths = []
            for index, upload in enumerate(job.uploads):
                if job.user_id in self.cancelled:
                    return
                remote = await bot.get_file(upload.file_id)
                if remote.file_size and remote.file_size > self.settings.max_input_bytes:
                    raise MediaError("Fayl belgilangan limitdan katta.")
                path = root / f"input-{index}.{'mp4' if upload.kind == 'video' else 'image'}"
                if self.settings.local_mode:
                    local = await asyncio.to_thread(Path(remote.file_path).resolve)
                    if (
                        not local.is_relative_to(self.settings.api_dir.resolve())
                        or not local.is_file()
                    ):
                        raise MediaError("Ichki Bot API fayl manzili noto'g'ri sozlangan.")
                    # Bir diskdagi hardlink: 100+ MB fayl RAMga olinmaydi va qayta ko'chirilmaydi.
                    await asyncio.to_thread(os.link, local, path)
                    self.api_files.setdefault(job.user_id, []).append(local)
                else:
                    await remote.download_to_drive(path, read_timeout=120, write_timeout=120)
                if path.stat().st_size > self.settings.max_input_bytes:
                    raise MediaError("Fayl belgilangan limitdan katta.")
                paths.append(path)
            if job.user_id in self.cancelled:
                return
            extension = (
                "mp4"
                if job.action.startswith("video-")
                else ("pdf" if job.action == "pdf" else "docx")
            )
            output = root / f"natija.{extension}"
            if job.action.startswith("video-"):
                progress.stage = "Video kichraytirilmoqda…"
                progress.percent = 0

                def update_progress(fraction: float):
                    progress.percent = min(99, int(fraction * 100))

                info = await convert_video(
                    paths[0],
                    output,
                    int(job.action[6:]),
                    self.settings,
                    on_progress=update_progress,
                )
                original_size = paths[0].stat().st_size
                new_size = output.stat().st_size
                caption = (
                    f"✅ Tayyor: {info.width} × {info.height}\n"
                    f"Hajm: {original_size / MB:.2f} MB → {new_size / MB:.2f} MB\n"
                    f"{(1 - new_size / original_size) * 100:.0f}% joy tejaldi."
                )
            else:
                progress.stage = (
                    "Rasmdagi matn o'qilmoqda…"
                    if job.action == "text"
                    else "Hujjat tayyorlanmoqda…"
                )
                function = {
                    "pdf": images_to_pdf,
                    "word": images_to_word,
                    "text": images_to_text_word,
                }[job.action]
                argument = self.settings if job.action == "text" else self.settings.max_image_pixels
                conversion = asyncio.create_task(
                    asyncio.to_thread(function, paths, output, argument)
                )
                try:
                    # To'xtatishda thread tugamaguncha vaqtinchalik papka o'chirilmaydi.
                    await asyncio.shield(conversion)
                except asyncio.CancelledError:
                    await conversion
                    raise
                caption = f"Tayyor: {len(paths)} ta rasm, {output.stat().st_size / MB:.1f} MB."
                if job.action == "text":
                    caption += "\nMatnni tekshiring: OCR harf va raqamlarda xato qilishi mumkin."
            if output.stat().st_size > self.settings.max_output_bytes:
                raise MediaError("Natija juda katta. Kamroq rasm yoki qisqaroq video yuboring.")
            if job.user_id in self.cancelled:
                return
            progress.stage = "Fayl tayyor. Telegram'ga yuborilmoqda…"
            progress.percent = None
            # Video ham fayl sifatida yuboriladi: Telegram qayta siqmaydi.
            if self.settings.local_mode:
                await bot.send_document(
                    chat_id=job.chat_id,
                    document=output.resolve().as_uri(),
                    filename=f"natija.{extension}",
                    caption=caption,
                    read_timeout=600,
                    write_timeout=600,
                )
            else:
                with output.open("rb") as stream:
                    await bot.send_document(
                        chat_id=job.chat_id,
                        document=stream,
                        filename=f"natija.{extension}",
                        caption=caption,
                        read_timeout=180,
                        write_timeout=180,
                    )
            # Oxirgi holatni ticker qayta yozib yubormasligi uchun uning matni ham almashtiriladi.
            progress.stage = "Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang."
            progress.percent = 100 if job.action.startswith("video-") else None
            await self.notify(bot, job, "✅ Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang.")
