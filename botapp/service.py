from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Bot
from telegram.error import BadRequest, TelegramError

from botapp.config import MB, Settings
from botapp.document_layout import images_to_editable_pdf, images_to_layout_word
from botapp.media import (
    RESOLUTIONS,
    MediaError,
    convert_video,
    images_to_pdf,
    images_to_text_word,
    images_to_word,
)
from botapp.office import convert_office
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
    queued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class CachedResult:
    file_id: str
    caption: str
    filename: str
    expires: float


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
        self.running: dict[int, asyncio.Task] = {}
        self.results: OrderedDict[tuple, CachedResult] = OrderedDict()
        self.timings: deque[dict] = deque(maxlen=50)

    def result_key(self, job: Job) -> tuple:
        # User-scoped, metadata only. No local media files are retained by this cache.
        return job.user_id, job.action, tuple(upload.file_id for upload in job.uploads)

    def cached_result(self, job: Job) -> CachedResult | None:
        key = self.result_key(job)
        item = self.results.get(key)
        if item and item.expires > time.monotonic():
            self.results.move_to_end(key)
            return item
        self.results.pop(key, None)
        return None

    def performance(self) -> dict:
        groups = {}
        for row in self.timings:
            group = groups.setdefault(
                row["action"],
                {
                    "jobs": 0,
                    "cached": 0,
                    "queue_s": 0,
                    "download_s": 0,
                    "convert_s": 0,
                    "upload_s": 0,
                },
            )
            group["jobs"] += 1
            group["cached"] += int(row["cached"])
            for key in ("queue_s", "download_s", "convert_s", "upload_s"):
                group[key] += row[key]
        return {
            action: {
                key: (round(value / group["jobs"], 3) if key.endswith("_s") else value)
                for key, value in group.items()
            }
            for action, group in groups.items()
        }

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
            or upload.kind in {"video", "docx", "pdf"}
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
            if action != "video-fast" and (
                not action.startswith("video-") or not action[6:].isdigit()
            ):
                raise MediaError("Video o'lchamini tanlang.")
            resolution = 0 if action == "video-fast" else int(action[6:])
            if resolution not in RESOLUTIONS and resolution != 0:
                raise MediaError("Video o'lchami noto'g'ri.")
            upload = session.uploads[0]
            if resolution and upload.width and upload.height:
                if resolution >= min(upload.width, upload.height):
                    raise MediaError("Pastroq o'lchamni yoki «Hajmni kamaytirish»ni tanlang.")
        elif session.uploads[0].kind in {"docx", "pdf"}:
            expected = "docx-pdf" if session.uploads[0].kind == "docx" else "pdf-docx"
            if action != expected:
                raise MediaError("Fayl formati bilan xizmat mos emas.")
        elif action not in {"pdf", "word", "text", "pdf-edit", "word-layout"}:
            raise MediaError("Rasm uchun PDF yoki Word tanlang.")
        job = Job(
            user_id,
            chat_id,
            tuple(session.uploads),
            action,
            status_id,
        )
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
            if task := self.running.get(user_id):
                task.cancel()
            return "❌ Bekor qilinyapti. Bu topshiriq natijasi yuborilmaydi."
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
                    task = asyncio.create_task(self.execute(bot, job))
                    self.running[job.user_id] = task
                    await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                await self.notify(bot, job, "❌ Topshiriq bekor qilindi.")
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
                self.running.pop(job.user_id, None)
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
        queue_seconds = max(0, time.monotonic() - job.queued_at)
        if cached := self.cached_result(job):
            progress.stage = "Oldingi tayyor natija Telegram'dan yuborilmoqda…"
            started = time.monotonic()
            try:
                await bot.send_document(
                    chat_id=job.chat_id,
                    document=cached.file_id,
                    filename=cached.filename,
                    caption=cached.caption,
                    read_timeout=max(600, self.settings.job_timeout),
                )
            except BadRequest:
                # Telegram may invalidate a file reference. Fall back to normal processing.
                self.results.pop(self.result_key(job), None)
            else:
                self.timings.append(
                    {
                        "action": job.action,
                        "cached": True,
                        "queue_s": queue_seconds,
                        "download_s": 0,
                        "convert_s": 0,
                        "upload_s": time.monotonic() - started,
                    }
                )
                progress.stage = "Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang."
                await self.notify(
                    bot, job, "✅ Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang."
                )
                return
        with tempfile.TemporaryDirectory(prefix="qulaybot-") as folder:
            root = Path(folder)
            paths = []
            download_started = time.monotonic()
            for index, upload in enumerate(job.uploads):
                if job.user_id in self.cancelled:
                    return
                remote = await bot.get_file(upload.file_id, read_timeout=self.settings.job_timeout)
                if remote.file_size and remote.file_size > self.settings.max_input_bytes:
                    raise MediaError("Fayl belgilangan limitdan katta.")
                suffix = {"video": "mp4", "image": "image", "pdf": "pdf", "docx": "docx"}
                path = root / f"input-{index}.{suffix[upload.kind]}"
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
            download_seconds = time.monotonic() - download_started
            logger.info(
                "Bosqich: %s; olish=%.2fs; fayllar=%d",
                job.action,
                time.monotonic() - download_started,
                len(paths),
            )
            if job.user_id in self.cancelled:
                return
            extension = (
                "mp4"
                if job.action.startswith("video-")
                else ("pdf" if job.action in {"pdf", "pdf-edit", "docx-pdf"} else "docx")
            )
            output = root / f"natija.{extension}"
            conversion_started = time.monotonic()
            if job.action in {"docx-pdf", "pdf-docx"}:
                progress.stage = (
                    "Word PDF'ga aylantirilmoqda…"
                    if job.action == "docx-pdf"
                    else ("PDF Word'ga aylantirilmoqda…")
                )
                scanned = await convert_office(paths[0], output, job.uploads[0].kind, self.settings)
                caption = "✅ PDF tayyor." if job.action == "docx-pdf" else "✅ Word tayyor."
                caption += (
                    "\nSahifalar va shriftlarni tekshiring: ayrim joylar farq qilishi mumkin."
                )
                if scanned:
                    caption += "\nSkanerlangan sahifalar rasm holida qolishi mumkin."
            elif job.action.startswith("video-"):
                progress.stage = "Video kichraytirilmoqda…"
                progress.percent = 0

                def update_progress(fraction: float):
                    progress.percent = min(99, int(fraction * 100))

                info = await convert_video(
                    paths[0],
                    output,
                    0 if job.action == "video-fast" else int(job.action[6:]),
                    self.settings,
                    on_progress=update_progress,
                    fast=job.action == "video-fast",
                )
                original_size = paths[0].stat().st_size
                new_size = output.stat().st_size
                caption = (
                    f"✅ Tayyor: {info.width} × {info.height}\n"
                    f"Hajm: {original_size / MB:.2f} MB → {new_size / MB:.2f} MB\n"
                    f"{(1 - new_size / original_size) * 100:.0f}% joy tejaldi."
                )
                if job.action == "video-fast":
                    caption += "\n⚡ Tez rejim: ko'pi bilan 360p / 15 kadr/soniya."
            else:
                progress.stage = (
                    "Rasmdagi matn o'qilmoqda…"
                    if job.action in {"text", "pdf-edit", "word-layout"}
                    else "Hujjat tayyorlanmoqda…"
                )
                function = {
                    "pdf": images_to_pdf,
                    "word": images_to_word,
                    "text": images_to_text_word,
                    "pdf-edit": images_to_editable_pdf,
                    "word-layout": images_to_layout_word,
                }[job.action]
                argument = (
                    self.settings
                    if job.action in {"text", "pdf-edit", "word-layout"}
                    else self.settings.max_image_pixels
                )
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
                if job.action in {"text", "pdf-edit", "word-layout"}:
                    caption += "\nMatnni tekshiring: OCR harf va raqamlarda xato qilishi mumkin."
                if job.action in {"pdf-edit", "word-layout"}:
                    caption += (
                        "\nTanilgan matn tahrirlanadi. Tanilmagan yozuvlar, logo va muhrlar "
                        "rasm bo'lib qolishi mumkin."
                    )
                if job.action == "pdf-edit":
                    caption += "\nMatnni o'zgartirish uchun PDF tahrirlash dasturi kerak."
            logger.info(
                "Bosqich: %s; aylantirish=%.2fs", job.action, time.monotonic() - conversion_started
            )
            conversion_seconds = time.monotonic() - conversion_started
            if output.stat().st_size > self.settings.max_output_bytes:
                raise MediaError("Natija juda katta. Kamroq rasm yoki qisqaroq video yuboring.")
            if job.user_id in self.cancelled:
                return
            progress.stage = "Fayl tayyor. Telegram'ga yuborilmoqda…"
            progress.percent = None
            upload_started = time.monotonic()
            # Video ham fayl sifatida yuboriladi: Telegram qayta siqmaydi.
            if self.settings.local_mode:
                sent = await bot.send_document(
                    chat_id=job.chat_id,
                    document=output.resolve().as_uri(),
                    filename=f"natija.{extension}",
                    caption=caption,
                    read_timeout=max(600, self.settings.job_timeout),
                    write_timeout=max(600, self.settings.job_timeout),
                )
            else:
                with output.open("rb") as stream:
                    sent = await bot.send_document(
                        chat_id=job.chat_id,
                        document=stream,
                        filename=f"natija.{extension}",
                        caption=caption,
                        read_timeout=180,
                        write_timeout=180,
                    )
            logger.info(
                "Bosqich: %s; yuborish=%.2fs", job.action, time.monotonic() - upload_started
            )
            self.timings.append(
                {
                    "action": job.action,
                    "cached": False,
                    "queue_s": queue_seconds,
                    "download_s": download_seconds,
                    "convert_s": conversion_seconds,
                    "upload_s": time.monotonic() - upload_started,
                }
            )
            if isinstance(
                file_id := getattr(getattr(sent, "document", None), "file_id", None), str
            ):
                self.results[self.result_key(job)] = CachedResult(
                    file_id,
                    caption,
                    f"natija.{extension}",
                    time.monotonic() + self.settings.session_ttl,
                )
                self.results.move_to_end(self.result_key(job))
                while len(self.results) > 128:
                    self.results.popitem(last=False)
            # Oxirgi holatni ticker qayta yozib yubormasligi uchun uning matni ham almashtiriladi.
            progress.stage = "Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang."
            progress.percent = 100 if job.action.startswith("video-") else None
            await self.notify(bot, job, "✅ Tayyor. Yangi xizmatni pastdagi tugmalardan tanlang.")
