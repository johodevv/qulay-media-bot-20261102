from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from botapp.config import MB, Settings
from botapp.media import RESOLUTIONS, MediaError
from botapp.office import OFFICE_MAX_BYTES
from botapp.service import ConversionService, Upload
from botapp.ui import CANCEL, SERVICES, STATUS, main_keyboard

logger = logging.getLogger(__name__)


def image_keyboard(
    nonce: str, ocr_enabled: bool, mode: str = "", *, advanced: bool = False
) -> InlineKeyboardMarkup:
    rows = []

    def add(label: str, action: str):
        rows.append([InlineKeyboardButton(label, callback_data=f"{nonce}:{action}")])

    if advanced and ocr_enabled:
        if mode != "word":
            add("📄 PDF — rasmli nusxa", "pdf")
        if mode != "pdf":
            add("📝 Word — faqat yozuvlar", "text")
            add("🖼 Word — rasmli nusxa", "word")
        add("⬅️ Orqaga", "menu-back")
    else:
        if mode != "word":
            add("✅ PDF tayyorlash", "pdf-edit" if ocr_enabled else "pdf")
        if mode != "pdf":
            add("✅ Word tayyorlash", "word-layout" if ocr_enabled else "word")
        if ocr_enabled:
            add("⚙️ Boshqa usullar", "menu-more")
    return InlineKeyboardMarkup(rows)


def image_prompt(count: int, mode: str, ocr_enabled: bool, *, advanced: bool = False) -> str:
    text = f"{count} ta rasm qabul qilindi.\n\n"
    if advanced and ocr_enabled:
        return text + (
            "Boshqa usullar:\n"
            "• Faqat yozuvlar — matnni olish, rasmdagi joylashuv saqlanmaydi.\n"
            "• Rasmli nusxa — ko'rinish saqlanadi, yozuvlarni o'zgartirib bo'lmaydi.\n\n"
            "Asosiy usulga qaytish uchun «Orqaga»ni bosing."
        )
    text += (
        "Tayyor faylda rasmdagi joylashuv saqlanadi, tanilgan yozuvlarni o'zgartira olasiz.\n\n"
        if ocr_enabled
        else "Rasmlar hujjatga joylanadi. Yozuvlar tahrirlanmaydi.\n\n"
    )
    if mode in {"pdf", "word"}:
        label = "PDF" if mode == "pdf" else "Word"
        text += f"Yana rasm qo'shing yoki «{label} tayyorlash»ni bosing."
    else:
        text += "Yana rasm qo'shing yoki PDF / Word tugmasini bosing."
    return text


def video_keyboard(nonce: str, short_edge: int = 0) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{nonce}:video-{value}")
        for value, label in RESOLUTIONS.items()
        if not short_edge or value < short_edge
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append(
        [InlineKeyboardButton("📦 Faqat hajmni kamaytirish", callback_data=f"{nonce}:video-0")]
    )
    return InlineKeyboardMarkup(rows)


class Handlers:
    def __init__(self, service: ConversionService, settings: Settings, ocr_enabled: bool):
        self.service = service
        self.settings = settings
        self.ocr_enabled = ocr_enabled

    async def allowed(self, update: Update) -> bool:
        user, chat = update.effective_user, update.effective_chat
        if not user or not chat:
            return False
        if chat.type != "private":
            if update.callback_query:
                await update.callback_query.answer(
                    "Botning shaxsiy chatidan foydalaning.", show_alert=True
                )
            elif update.effective_message:
                await update.effective_message.reply_text("Botning shaxsiy chatidan foydalaning.")
            return False
        if self.settings.allowed_user_ids and user.id not in self.settings.allowed_user_ids:
            if update.callback_query:
                await update.callback_query.answer("Bot hozir yopiq sinovda.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("Bot hozir yopiq sinovda.")
            return False
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        await update.effective_message.reply_text(
            "Assalomu alaykum! 👋\n\n"
            "Pastdagi tugmalardan xizmatni tanlang:\n"
            "🎬 Video sifatini pasaytirish va hajmini kamaytirish\n"
            "📄 Rasm → PDF\n"
            "📝 Rasm → Word\n\n"
            "📑 Word → PDF yoki PDF → Word\n\n"
            "Xizmatni tanlang → fayl yuboring → kerakli natijani belgilang.\n"
            "Bir hujjat uchun bir nechta rasm yuborishingiz mumkin.\n\n"
            f"Limit: jami {self.settings.max_input_bytes // MB} MB, "
            f"{self.settings.max_images} ta rasm, "
            f"video {self.settings.max_video_seconds // 60} daqiqagacha.\n"
            "/privacy — fayllar haqida",
            reply_markup=main_keyboard(),
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        user_id = update.effective_user.id
        await update.effective_message.reply_text(
            self.service.status_text(user_id), reply_markup=main_keyboard()
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.allowed(update):
            await update.effective_message.reply_text(
                self.service.cancel(update.effective_user.id), reply_markup=main_keyboard()
            )

    async def privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.allowed(update):
            await update.effective_message.reply_text(
                "Fayllar bajarish vaqtida bot serverida vaqtincha saqlanadi va ish tugagach "
                "o'chiriladi. Elektr uzilishi yoki majburiy to'xtashda vaqtinchalik nusxa qolishi "
                "mumkin. Telegram chatidagi nusxalar alohida saqlanadi.\n\n"
                "Navbat va tanlovlar xotirada: bot qayta ishga tushsa qayta yuborish kerak. "
                "Xizmat hujjatini yuborishdan oldin tashkilotingiz tartibini tekshiring."
            )

    async def receive(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        message = update.effective_message
        text = getattr(message, "text", None)
        if text == STATUS:
            await self.status(update, context)
            return
        if text == CANCEL:
            await self.cancel(update, context)
            return
        if text in SERVICES:
            mode = SERVICES[text]
            try:
                self.service.select_service(update.effective_user.id, mode)
            except MediaError as exc:
                await message.reply_text(str(exc), reply_markup=main_keyboard())
                return
            instructions = {
                "video": "🎬 Videoni yuboring. "
                "Keyin pastroq o'lcham yoki hajmni kamaytirishni tanlang.",
                "pdf": "📄 PDF uchun rasmlarni kerakli tartibda yuboring.\n"
                "Hammasini yuborgach «PDF tayyorlash»ni bosing.",
                "word": "📝 Word uchun rasmlarni yuboring.\n"
                "Hammasini yuborgach «Word tayyorlash»ni bosing.",
                "docx-pdf": "📑 Word faylini (.docx) yuboring.\n"
                "PDF avtomatik tayyorlanadi. Limit: 30 MB.",
                "pdf-docx": "📃 PDF faylini yuboring. Word avtomatik tayyorlanadi.\n"
                "Limit: 30 MB, 50 sahifa. Skanerlangan sahifalar rasm bo'lib qolishi mumkin.",
            }
            await message.reply_text(instructions[mode], reply_markup=main_keyboard())
            return
        if message.photo:
            media, kind = message.photo[-1], "image"
        elif message.video:
            media, kind = message.video, "video"
        elif message.document:
            media = message.document
            mime = media.mime_type or ""
            if mime.startswith("image/"):
                kind = "image"
            elif mime.startswith("video/"):
                kind = "video"
            elif mime == "application/pdf" or (media.file_name or "").lower().endswith(".pdf"):
                kind = "pdf"
            elif (
                mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                or (media.file_name or "").lower().endswith(".docx")
            ):
                kind = "docx"
            else:
                await message.reply_text("Rasm, video, Word (.docx) yoki PDF faylini yuboring.")
                return
        else:
            await message.reply_text(
                "Pastdagi tugmalardan xizmatni tanlang.", reply_markup=main_keyboard()
            )
            return
        selected = self.service.sessions.get(update.effective_user.id)
        mode = selected.mode if selected else ""
        if mode in {"docx-pdf", "pdf-docx"}:
            expected = "docx" if mode == "docx-pdf" else "pdf"
            if kind != expected:
                await message.reply_text(
                    "Word (.docx) faylini yuboring."
                    if expected == "docx"
                    else "PDF faylini yuboring."
                )
                return
        if kind in {"docx", "pdf"}:
            if mode and mode not in {"docx-pdf", "pdf-docx"}:
                await message.reply_text(
                    "Pastdan «Word → PDF» yoki «PDF → Word» xizmatini tanlang."
                )
                return
            if not self.settings.soffice:
                await message.reply_text("Word ↔ PDF xizmati serverda hali tayyor emas.")
                return
            if not media.file_size or media.file_size > OFFICE_MAX_BYTES:
                await message.reply_text("Word/PDF fayli 30 MB dan oshmasin. Video limiti alohida.")
                return
        if mode in {"pdf", "word"} and kind != "image":
            await message.reply_text(
                "Bu xizmat uchun rasm yuboring. Video uchun pastdagi "
                "«Video sifatini pasaytirish»ni tanlang."
            )
            return
        if mode == "video" and kind != "video":
            await message.reply_text(
                "Bu xizmat uchun video yuboring. Rasm uchun PDF yoki Word xizmatini tanlang."
            )
            return
        if kind == "video" and not (self.settings.ffmpeg and self.settings.ffprobe):
            await message.reply_text("Video xizmati hali sozlanmagan. Rasm → PDF/Word ishlaydi.")
            return
        if kind == "video" and getattr(media, "duration", 0) > self.settings.max_video_seconds:
            await message.reply_text(
                f"Video {self.settings.max_video_seconds // 60} daqiqadan uzun bo'lmasin."
            )
            return
        try:
            session = self.service.add_upload(
                update.effective_user.id,
                Upload(
                    media.file_id,
                    media.file_size or 0,
                    kind,
                    getattr(media, "width", 0),
                    getattr(media, "height", 0),
                ),
            )
        except MediaError as exc:
            await message.reply_text(str(exc))
            return
        if kind in {"docx", "pdf"}:
            action = "docx-pdf" if kind == "docx" else "pdf-docx"
            status = await message.reply_text("⏳ Fayl qabul qilindi. Hujjat tayyorlanyapti…")
            try:
                self.service.enqueue(
                    update.effective_user.id,
                    update.effective_chat.id,
                    session.nonce,
                    action,
                    status.message_id,
                )
            except MediaError as exc:
                await status.edit_text(str(exc))
            return
        if kind == "image":
            text = image_prompt(len(session.uploads), session.mode, self.ocr_enabled)
            keyboard = image_keyboard(session.nonce, self.ocr_enabled, session.mode)
        else:
            text = (
                "Video qabul qilindi. Pastroq o'lchamni yoki faqat hajmni kamaytirishni tanlang.\n"
                "Tik videoning yo'nalishi saqlanadi."
            )
            short_edge = min(getattr(media, "width", 0), getattr(media, "height", 0))
            keyboard = video_keyboard(session.nonce, short_edge)
        await message.reply_text(text, reply_markup=keyboard)

    async def choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        query = update.callback_query
        await query.answer()
        try:
            nonce, action = query.data.split(":", 1)
            session = self.service.get_session(update.effective_user.id, nonce)
            if action in {"menu-more", "menu-back"}:
                advanced = action == "menu-more"
                await query.edit_message_text(
                    text=image_prompt(
                        len(session.uploads), session.mode, self.ocr_enabled, advanced=advanced
                    ),
                    reply_markup=image_keyboard(
                        nonce, self.ocr_enabled, session.mode, advanced=advanced
                    ),
                )
                return
            if action in {"text", "pdf-edit", "word-layout"} and not self.ocr_enabled:
                raise MediaError("Yozuvni tanish hozir ishlamayapti. Rasmni qayta yuboring.")
            # Eski tugma uchun ortiqcha holat xabari yubormaymiz.
            status = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Topshiriq navbatga qabul qilindi. Natija shu chatga yuboriladi.",
            )
            try:
                self.service.enqueue(
                    update.effective_user.id,
                    update.effective_chat.id,
                    nonce,
                    action,
                    status.message_id,
                )
            except MediaError as exc:
                await status.edit_text(str(exc))
                return
            # Worker holatni bundan keyin yangilaydi; bu yerda xabarni qayta tahrirlamaymiz.
            await query.edit_message_reply_markup(reply_markup=None)
        except (MediaError, ValueError) as exc:
            text = str(exc) if isinstance(exc, MediaError) else "Tugma eskirgan. /start ni bosing."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram hodisasi xatosi: %s", type(context.error).__name__)

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler(["start", "help"], self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(CommandHandler("privacy", self.privacy))
        application.add_handler(CallbackQueryHandler(self.choose))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.receive))
        application.add_error_handler(self.error)
