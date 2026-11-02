from telegram import ReplyKeyboardMarkup

VIDEO = "🎬 Video sifatini pasaytirish"
PDF = "📄 Rasm → PDF"
WORD = "📝 Rasm → Word"
STATUS = "⏳ Jarayon holati"
CANCEL = "❌ Bekor qilish"
SERVICES = {VIDEO: "video", PDF: "pdf", WORD: "word"}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[VIDEO], [PDF, WORD], [STATUS, CANCEL]],
        resize_keyboard=True,
        is_persistent=True,
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
