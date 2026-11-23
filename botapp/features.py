"""Admin console and private quizzes from uploaded documents."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import tempfile
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, TypeHandler

from botapp.media import MediaError
from botapp.quiz import LABELS, QUIZ_MAX_BYTES, export_quiz, read_quiz_docx, read_quiz_pdf
from botapp.quiz_choices import make_variant_questions
from botapp.store import BotStore
from botapp.ui import ADMIN, CANCEL, QUIZ, SERVICES, STATUS

logger = logging.getLogger(__name__)
TASHKENT = timezone(timedelta(hours=5))
QUIZ_UPLOADS = {"parse", "parse-word"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def date_text(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, TASHKENT).strftime("%d.%m.%Y %H:%M")


class FeatureHandlers:
    def __init__(self, parent, store: BotStore):
        self.parent, self.store = parent, store
        self.pending: dict[int, tuple[str, float]] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.uploads: dict[int, deque] = {}
        self.work_lock = asyncio.Semaphore(1)

    def register(self, application):
        application.add_handler(TypeHandler(Update, self.track), group=-1)
        application.add_handler(CommandHandler("id", self.identity))
        application.add_handler(
            CommandHandler(["admin", "stats", "users", "admins"], self.admin_command)
        )
        application.add_handler(CommandHandler(["addadmin", "removeadmin"], self.admin_change))
        application.add_handler(CommandHandler("backup", self.backup))
        application.add_handler(CommandHandler("restore", self.restore))
        application.add_handler(CommandHandler("quiz", self.quiz_command))
        application.add_handler(CallbackQueryHandler(self.admin_callback, pattern=r"^admin:"))
        application.add_handler(CallbackQueryHandler(self.quiz_callback, pattern=r"^quiz:"))

    async def track(self, update, context):
        user, chat = update.effective_user, update.effective_chat
        if user and chat and chat.type == "private" and not user.is_bot:
            allowed = self.parent.settings.allowed_user_ids
            if not allowed or user.id in allowed:
                self.store.touch(user, update.update_id)

    async def identity(self, update, context):
        if await self.parent.allowed(update):
            await update.effective_message.reply_text(
                f"Sizning Telegram ID: {update.effective_user.id}"
            )

    async def _admin_allowed(self, update, *, owner=False):
        if not await self.parent.allowed(update):
            return False
        user_id = update.effective_user.id
        valid = (
            user_id == self.store.owner_id and bool(self.store.owner_id)
            if owner
            else self.store.is_admin(user_id)
        )
        if not valid:
            text = "Bu amal faqat bosh admin uchun." if owner else "Bu bo'lim faqat adminlar uchun."
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            else:
                await update.effective_message.reply_text(text)
        return valid

    def _admin_view(self, actor: int, action: str, page: int = 0):
        stats = self.store.statistics()
        if action == "users":
            rows = self.store.users(page)
            text = f"👥 Foydalanuvchilar · {stats['total']} kishi\nSahifa: {page + 1}\n\n"
            text += (
                "\n\n".join(
                    f"{page * 10 + index}. {row['name']}\n"
                    f"{'@' + row['username'] if row['username'] else 'Username yo‘q'}\n"
                    f"ID: {row['id']}\n"
                    f"So'nggi faollik: {date_text(row['last_seen'])}"
                    for index, row in enumerate(rows, 1)
                )
                or "Hozircha foydalanuvchi yo'q."
            )
            buttons = []
            if page:
                buttons.append(Button("⬅️", callback_data=f"admin:users:{page - 1}"))
            if (page + 1) * 10 < stats["total"]:
                buttons.append(Button("➡️", callback_data=f"admin:users:{page + 1}"))
            keyboard = [buttons] if buttons else []
        elif action == "admins":
            text = "🛡 Adminlar\n\n" + "\n".join(
                f"{'👑 Bosh admin' if user_id == self.store.owner_id else '🛡 Admin'}: {user_id}"
                for user_id in self.store.admins()
            )
            keyboard = []
            if actor == self.store.owner_id:
                keyboard.append([Button("➕ Admin qo'shish", callback_data="admin:add")])
                keyboard.append([Button("➖ Adminni olib tashlash", callback_data="admin:remove")])
                keyboard.append([Button("💾 Statistika zaxirasi", callback_data="admin:backup")])
        else:
            text = (
                "📊 Bot statistikasi\n\n"
                f"Jami foydalanuvchilar: {stats['total']}\n"
                f"Bugun faol: {stats['today']}\nOxirgi 7 kunda faol: {stats['week']}\n"
                f"Oxirgi 30 kunda faol: {stats['month']}\n"
                f"Qabul qilingan harakatlar: {stats['updates']}\nAdminlar: {stats['admins']}\n\n"
                f"Hisobga olish boshlandi: {date_text(stats['started'])}\n"
                "Faollik — botga xabar yuborish yoki tugma bosish. Vaqt: Toshkent."
            )
            keyboard = []
        keyboard += [
            [
                Button("📊 Statistika", callback_data="admin:stats"),
                Button("👥 Foydalanuvchilar", callback_data="admin:users:0"),
            ],
            [Button("🛡 Adminlar", callback_data="admin:admins")],
        ]
        return text, Markup(keyboard)

    async def admin_command(self, update, context):
        if not await self._admin_allowed(update):
            return
        self.pending.pop(update.effective_user.id, None)
        command = update.effective_message.text.split()[0].split("@")[0][1:]
        text, markup = self._admin_view(update.effective_user.id, command)
        await update.effective_message.reply_text(text, reply_markup=markup)

    async def admin_callback(self, update, context):
        if not await self._admin_allowed(update):
            return
        query, actor = update.callback_query, update.effective_user.id
        action = query.data.split(":")[1]
        if action in {"add", "remove", "backup"}:
            if actor != self.store.owner_id:
                await query.answer("Faqat bosh admin uchun.", show_alert=True)
                return
            await query.answer()
            if action == "backup":
                await self.backup(update, context)
                return
            self.pending[actor] = (action, time.monotonic() + 300)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=("Admin qilinadigan" if action == "add" else "Adminlikdan olinadigan")
                + " foydalanuvchining Telegram ID yoki @username'ini yuboring.\n"
                "U avval botga /start yuborgan bo'lsin. Bekor qilish: /cancel",
            )
            return
        await query.answer()
        try:
            parts = query.data.split(":")
            page = int(parts[2]) if len(parts) == 3 else 0
            if not 0 <= page <= 100000 or action not in {"stats", "users", "admins"}:
                raise ValueError
            text, markup = self._admin_view(actor, action, page)
            try:
                await query.edit_message_text(text, reply_markup=markup)
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    raise
        except ValueError:
            await context.bot.send_message(
                update.effective_chat.id, "Tugma eskirgan. /admin ni bosing."
            )

    async def admin_change(self, update, context):
        if not await self._admin_allowed(update, owner=True):
            return
        if len(context.args) != 1:
            await update.effective_message.reply_text(
                "Namuna: /addadmin 123456789 yoki /removeadmin 123456789"
            )
            return
        add = update.effective_message.text.split()[0].split("@")[0] == "/addadmin"
        await self._change_role(update, context.args[0], add)

    async def _change_role(self, update, value, add):
        try:
            target = self.store.resolve_user(value.strip())
            self.store.change_admin(update.effective_user.id, target, add=add)
            await update.effective_message.reply_text(
                f"✅ {target}: " + ("admin qo'shildi." if add else "admin huquqi olindi.")
            )
        except MediaError as exc:
            await update.effective_message.reply_text(str(exc))

    async def backup(self, update, context):
        if not await self._admin_allowed(update, owner=True):
            return
        data = io.BytesIO(self.store.export_metadata(update.effective_user.id))
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=data,
            filename="foydalanuvchilar-zaxirasi.json",
            caption=(
                "Foydalanuvchilar va adminlar zaxirasi. Shaxsiy saqlang. "
                "Tiklash: /restore. PDF mazmuni kiritilmagan."
            ),
        )

    async def restore(self, update, context):
        if await self._admin_allowed(update, owner=True):
            self.pending[update.effective_user.id] = ("restore", time.monotonic() + 300)
            await update.effective_message.reply_text(
                "Bot bergan foydalanuvchilar-zaxirasi.json faylini yuboring. "
                "Ma'lumotlar mavjud ro'yxatga qo'shiladi. Bekor qilish: /cancel"
            )

    async def quiz_command(self, update, context):
        if await self.parent.allowed(update):
            await self.select_quiz(update)

    async def select_quiz(self, update):
        user_id = update.effective_user.id
        try:
            self.parent.service.select_service(user_id, "quiz")
        except MediaError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        self.pending.pop(user_id, None)
        await update.effective_message.reply_text(
            "🧩 Savol-javobli Word (.docx) yoki PDF yuboring. "
            "Har bir fayldan alohida test tayyorlanadi.\n\n"
            "Bot javob variantlarini ko'rsatadi, tanlovingizni tekshiradi "
            "va yakunda ballni hisoblaydi.\n\n"
            "Word namuna: raqamlangan savol, keyingi qatorda «Javob:» yoki «Ж:».\n"
            "Limit: har faylga 10 MB va 100 savol. "
            "PDF: 30 matnli yoki 5 skanerlangan sahifa.",
            reply_markup=self.parent.keyboard(user_id),
        )

    async def handle_message(self, update, context) -> bool:
        message, user_id = update.effective_message, update.effective_user.id
        text = getattr(message, "text", None)
        if text == ADMIN:
            await self.admin_command(update, context)
            return True
        if text in {QUIZ, "🧩 PDFdan test"}:
            await self.select_quiz(update)
            return True
        if text in {CANCEL, STATUS} or text in SERVICES:
            self.pending.pop(user_id, None)
            return False
        pending = self.pending.get(user_id)
        if pending:
            self.pending.pop(user_id, None)
            if pending[1] > time.monotonic() and pending[0] == "restore":
                if await self._admin_allowed(update, owner=True):
                    doc = getattr(message, "document", None)
                    if not doc or not (doc.file_name or "").endswith(".json"):
                        await message.reply_text(
                            "Bot bergan JSON faylni yuboring. Qayta boshlash: /restore"
                        )
                    elif not doc.file_size or doc.file_size > QUIZ_MAX_BYTES:
                        await message.reply_text("Zaxira fayli 10 MB dan oshmasin.")
                    else:
                        await self.launch(update, context, "restore", doc.file_id)
                return True
            if pending[1] > time.monotonic() and text:
                if await self._admin_allowed(update, owner=True):
                    await self._change_role(update, text, pending[0] == "add")
                return True
        session = self.parent.service.sessions.get(user_id)
        if not session or session.mode != "quiz":
            return False
        document = getattr(message, "document", None)
        filename = (document.file_name or "") if document else ""
        mime_type = document.mime_type if document else None
        if filename.lower().endswith(".docx") or mime_type == DOCX_MIME:
            action = "parse-word"
        elif filename.lower().endswith(".pdf") or mime_type == "application/pdf":
            action = "parse"
        else:
            await message.reply_text("Test uchun Word (.docx) yoki PDF faylini yuboring.")
            return True
        if not document.file_size or not 0 < document.file_size <= QUIZ_MAX_BYTES:
            await message.reply_text("Har bir test fayli 10 MB dan oshmasin.")
            return True
        session.touched = time.monotonic()
        await self.launch(update, context, action, document.file_id, filename)
        return True

    async def launch(self, update, context, action, value, filename=""):
        user_id, chat_id = update.effective_user.id, update.effective_chat.id
        queue = self.uploads.get(user_id)
        if (
            queue is not None
            and action in QUIZ_UPLOADS
            and user_id not in self.parent.service.cancelled
        ):
            if len(queue) >= 4:
                await update.effective_message.reply_text(
                    "Navbat to'ldi. Biroz kutib, qayta yuboring."
                )
                return
            queue.append((action, value, filename))
            return
        if user_id in self.parent.service.busy:
            await context.bot.send_message(
                chat_id, "Avvalgi topshiriq tugashini kuting yoki /cancel bosing."
            )
            return
        if len(self.tasks) >= self.parent.settings.max_queue:
            await context.bot.send_message(
                chat_id, "Test tayyorlash navbati to'ldi. Birozdan so'ng urinib ko'ring."
            )
            return
        status = await self.job_status(context.bot, chat_id, action, filename)
        self.parent.service.busy.add(user_id)
        if action in QUIZ_UPLOADS:
            self.uploads[user_id] = deque()
        task = asyncio.create_task(
            self.run_jobs(context.bot, user_id, chat_id, status, action, value, filename)
        )
        self.tasks[user_id] = task
        task.add_done_callback(lambda done: self._release(user_id, done))

    def _release(self, user_id, task):
        if self.tasks.get(user_id) is task:
            self.parent.service.busy.discard(user_id)
            self.parent.service.cancelled.discard(user_id)
            self.tasks.pop(user_id, None)
            self.uploads.pop(user_id, None)

    @staticmethod
    async def job_status(bot, chat_id, action, filename):
        text = "⏳ Fayl o'qilmoqda…" if action in QUIZ_UPLOADS else "⏳ Hujjat tayyorlanmoqda…"
        if filename:
            text += "\n" + filename[:120]
        return await bot.send_message(chat_id, text)

    async def run_jobs(self, bot, user_id, chat_id, status, action, value, filename):
        try:
            await self.work(bot, user_id, chat_id, status, action, value, filename)
            queue = self.uploads.get(user_id)
            while queue:
                action, value, filename = queue.popleft()
                status = await self.job_status(bot, chat_id, action, filename)
                await self.work(bot, user_id, chat_id, status, action, value, filename)
        finally:
            self._release(user_id, asyncio.current_task())

    async def work(self, bot, user_id, chat_id, status, action, value, filename):
        try:
            async with self.work_lock:
                with tempfile.TemporaryDirectory(prefix="qb-quiz-") as folder:
                    root = Path(folder)
                    if action == "restore":
                        path = root / "backup.json"
                        await self.download(bot, value, path)
                        count = self.store.restore_metadata(
                            user_id, await asyncio.to_thread(path.read_bytes)
                        )
                        await status.edit_text(f"✅ Zaxira tiklandi: {count} foydalanuvchi.")
                    elif action in QUIZ_UPLOADS:
                        path = root / ("input.docx" if action == "parse-word" else "input.pdf")
                        await self.download(bot, value, path)
                        if action == "parse-word":
                            result = await self.thread(read_quiz_docx, path)
                        else:
                            result = await self.thread(read_quiz_pdf, path, self.parent.settings)
                        questions = await self.thread(make_variant_questions, result["questions"])
                        quiz_id = self.store.save_quiz(user_id, questions, result["scanned"])
                        note = (
                            "\nSkanerlangan matn tanildi. Harf va javoblarni tekshiring."
                            if result["scanned"]
                            else ""
                        )
                        if any(q.get("generated_options") for q in questions):
                            note += (
                                "\nVariantlar fayldagi boshqa javoblardan tuzildi. "
                                "Boshlashdan oldin Word/PDF nusxasini tekshiring."
                            )
                        if questions[0].get("source_variant"):
                            note += "\nA/B/C/D variantlari manba matnidan tuzildi."
                        first = questions[0]
                        preview = f"\n\nBirinchi savol: {first['question']}\n" + "\n".join(
                            f"{LABELS[i]}) {option}"
                            for i, option in enumerate(first.get("options", []))
                        )
                        if len(preview) > 2500:
                            preview = preview[:2400] + "…\nTo'liq matn Word/PDF nusxasida."
                        await status.edit_text(
                            f"✅ {len(questions)} ta test savoli tayyor."
                            + (f"\n{filename[:120]}" if filename else "")
                            + f"{note}{preview}"
                            "\n\nTestni boshlang yoki varaqani yuklab oling.",
                            reply_markup=self.quiz_menu(quiz_id),
                        )
                    else:
                        quiz = self.store.quiz(user_id, value)
                        ext = "docx" if action == "word" else "pdf"
                        output = root / f"test-savollari.{ext}"
                        await self.thread(export_quiz, quiz["questions"], output, action)
                        with output.open("rb") as stream:
                            await bot.send_document(
                                chat_id,
                                document=stream,
                                filename=output.name,
                                caption="✅ Test tayyor. Javoblar kaliti hujjatning oxirida.",
                                read_timeout=120,
                                write_timeout=120,
                            )
                        await status.edit_text(
                            "✅ Test varaqasi yuborildi.", reply_markup=self.quiz_menu(value)
                        )
        except asyncio.CancelledError:
            await self._status(status, "❌ Test tayyorlash bekor qilindi.")
            raise
        except MediaError as exc:
            await self._status(status, str(exc))
        except TelegramError:
            logger.warning("Test natijasini Telegram orqali yetkazib bo'lmadi.")
        except Exception as exc:
            logger.error("Test tayyorlash xatosi: %s", type(exc).__name__)
            await self._status(
                status, "Test tayyorlanmadi. Fayl tuzilishini tekshirib qayta yuboring."
            )

    async def download(self, bot, file_id, output):
        remote = await bot.get_file(file_id, read_timeout=90)
        if remote.file_size and remote.file_size > QUIZ_MAX_BYTES:
            raise MediaError("Fayl 10 MB dan oshmasin.")
        if self.parent.settings.local_mode:
            source = await asyncio.to_thread(Path(remote.file_path).resolve)
            if (
                not source.is_relative_to(self.parent.settings.api_dir.resolve())
                or not source.is_file()
            ):
                raise MediaError("Ichki fayl manzili noto'g'ri.")
            if source.stat().st_size > QUIZ_MAX_BYTES:
                raise MediaError("Fayl 10 MB dan oshmasin.")
            try:
                await self.thread(shutil.copyfile, source, output)
            finally:
                await asyncio.to_thread(source.unlink, missing_ok=True)
        else:
            await remote.download_to_drive(output, read_timeout=90, write_timeout=90)
        if not 0 < output.stat().st_size <= QUIZ_MAX_BYTES:
            raise MediaError("Fayl 10 MB dan oshmasin.")

    @staticmethod
    async def thread(function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    @staticmethod
    async def _status(status, text):
        try:
            await status.edit_text(text)
        except TelegramError:
            pass

    @staticmethod
    def quiz_menu(quiz_id):
        return Markup(
            [
                [Button("▶️ Testni boshlash", callback_data=f"quiz:start:{quiz_id}")],
                [
                    Button("📝 Word olish", callback_data=f"quiz:word:{quiz_id}"),
                    Button("📄 PDF olish", callback_data=f"quiz:pdf:{quiz_id}"),
                ],
            ]
        )

    def render_attempt(self, user_id, quiz_id):
        quiz = self.store.quiz(user_id, quiz_id)
        attempt = self.store.attempt(user_id, quiz_id)
        count, position = len(quiz["questions"]), attempt["position"]
        if attempt["phase"] == "done":
            score_label = (
                "O'zingiz to'g'ri deb baholagan javoblar"
                if "model_answer" in quiz["questions"][0]
                else "To'g'ri javoblar"
            )
            return (
                f"🏁 Test tugadi!\n\n{score_label}: {attempt['score']} / {count}\n"
                f"Natija: {attempt['score'] / count * 100:.0f}%",
                self.quiz_menu(quiz_id),
            )
        question = quiz["questions"][position]
        if "model_answer" in question:
            text = f"🧩 Savol {position + 1} / {count}\n\n{question['question']}"
            if attempt["phase"] == "question":
                button = Button(
                    "Javobni ko'rish", callback_data=f"quiz:reveal:{quiz_id}:{position}"
                )
                return text, Markup([[button]])
            answer = question["model_answer"]
            available = 3650 - len(text)
            buttons = []
            if len(answer) > available:
                answer = answer[:available].rstrip() + "…"
                buttons.append(
                    [Button("To'liq javob (.txt)", callback_data=f"quiz:full:{quiz_id}:{position}")]
                )
            buttons.append(
                [
                    Button("✅ Topdim", callback_data=f"quiz:answer:{quiz_id}:{position}:1"),
                    Button("🔁 Takrorlayman", callback_data=f"quiz:answer:{quiz_id}:{position}:0"),
                ]
            )
            return f"{text}\n\nJavob:\n{answer}\n\nO'zingizni baholang.", Markup(buttons)
        if attempt["phase"] == "feedback":
            chosen = json.loads(attempt["answers"])[-1]
            correct = question["correct"]
            text = "✅ To'g'ri!" if chosen == correct else "❌ Noto'g'ri."
            text += (
                f"\nTo'g'ri javob: {LABELS[correct]}) {question['options'][correct]}"
                f"\nBall: {attempt['score']} / {count}"
            )
            buttons = [
                [
                    Button(
                        "🏁 Natijani ko'rish" if position + 1 == count else "Keyingi savol ➡️",
                        callback_data=f"quiz:next:{quiz_id}:{position}",
                    )
                ]
            ]
            if "source_answer" in question:
                buttons.append(
                    [Button("Asl javob (.txt)", callback_data=f"quiz:full:{quiz_id}:{position}")]
                )
            return text, Markup(buttons)
        text = f"🧩 Savol {position + 1} / {count}\n\n{question['question']}\n\n" + "\n\n".join(
            f"{LABELS[i]}) {option}" for i, option in enumerate(question["options"])
        )
        buttons = [
            Button(LABELS[i], callback_data=f"quiz:answer:{quiz_id}:{position}:{i}")
            for i in range(len(question["options"]))
        ]
        return text, Markup([buttons])

    async def quiz_callback(self, update, context):
        if not await self.parent.allowed(update):
            return
        query, user_id = update.callback_query, update.effective_user.id
        try:
            parts = query.data.split(":")
            action, quiz_id = parts[1:3]
            quiz = self.store.quiz(user_id, quiz_id)  # Ownership check before any action.
            if action in {"word", "pdf"}:
                await query.answer()
                await self.launch(update, context, action, quiz_id)
                return
            if action == "start" and len(parts) == 3:
                self.store.start_attempt(user_id, quiz_id)
            elif action == "reveal" and len(parts) == 4:
                self.store.reveal_answer(user_id, quiz_id, int(parts[3]))
            elif action == "full" and len(parts) == 4:
                position = int(parts[3])
                attempt = self.store.attempt(user_id, quiz_id)
                if (
                    attempt["phase"] not in {"review", "feedback"}
                    or attempt["position"] != position
                ):
                    raise MediaError("Bu tugma eskirgan.")
                question = quiz["questions"][position]
                answer = question.get("source_answer") or question.get("model_answer")
                if not answer:
                    raise MediaError("Asl javob fayli mavjud emas.")
                await query.answer()
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=io.BytesIO(answer.encode("utf-8")),
                    filename=f"{position + 1}-savol-javobi.txt",
                )
                return
            elif action == "answer" and len(parts) == 5:
                self.store.answer(user_id, quiz_id, int(parts[3]), int(parts[4]))
            elif action == "next" and len(parts) == 4:
                self.store.next_question(user_id, quiz_id, int(parts[3]))
            else:
                raise ValueError
            await query.answer()
            text, markup = self.render_attempt(user_id, quiz_id)
            await query.edit_message_text(text, reply_markup=markup)
        except (MediaError, ValueError, IndexError) as exc:
            await query.answer(
                str(exc) if isinstance(exc, MediaError) else "Test tugmasi eskirgan.",
                show_alert=True,
            )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise

    def cancel(self, user_id):
        self.pending.pop(user_id, None)
        self.store.cancel_attempt(user_id)
        if task := self.tasks.get(user_id):
            task.cancel()

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
