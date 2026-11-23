from telegram import ReplyKeyboardMarkup

VIDEO = "🎬 Video hajmini kamaytirish"
PDF = "📄 Rasm → PDF"
WORD = "📝 Rasm → Word"
WORD_PDF = "📑 Word → PDF"
PDF_WORD = "📃 PDF → Word"
STATUS = "⏳ Jarayon holati"
CANCEL = "❌ Bekor qilish"
QUIZ = "🧩 Word / PDFdan test"
AI_QUIZ = "✨ Gemini bilan PDF testi"
ADMIN = "🛡 Admin paneli"
SERVICES = {
    "🎬 Video sifatini pasaytirish": "video",
    VIDEO: "video",
    PDF: "pdf",
    WORD: "word",
    WORD_PDF: "docx-pdf",
    PDF_WORD: "pdf-docx",
}


def main_keyboard(is_admin: bool = False, *, ai_enabled: bool = False) -> ReplyKeyboardMarkup:
    rows = [[VIDEO], [PDF, WORD], [WORD_PDF, PDF_WORD], [QUIZ]]
    if ai_enabled:
        rows.append([AI_QUIZ])
    rows.append([STATUS, CANCEL])
    if is_admin:
        rows.append([ADMIN])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=False,
        input_field_placeholder="Xizmatni tanlang yoki fayl yuboring",
    )


def progress_text(stage: str, percent: int | None, elapsed: float) -> str:
    minutes, seconds = divmod(int(elapsed), 60)
    clock = f"{minutes}:{seconds:02d}"
    if percent is None:
        return f"⏳ {stage}\nO'tgan vaqt: {clock}"
    percent = min(100, max(0, percent))
    filled = percent // 10
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return f"🎬 {stage}\n{bar} {percent}%\nO'tgan vaqt: {clock}"
