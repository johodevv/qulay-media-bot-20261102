"""Token talab qilmaydigan, haqiqiy media natijalari bilan mahalliy sinov."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from botapp.config import ROOT, Settings  # noqa: E402
from botapp.media import (  # noqa: E402
    convert_video,
    images_to_pdf,
    images_to_text_word,
    images_to_word,
    ocr_available,
    run_process,
)


async def main():
    settings = Settings.from_env(require_token=False)
    output = ROOT / "test-results" / "demo"
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = (
        ImageFont.truetype(str(font_path), 55) if font_path.exists() else ImageFont.load_default()
    )
    paths = []
    for index in (1, 2):
        path = output / f"rasm-{index}.png"
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        draw.text((100, 150), "QULAY MEDIA BOT", font=font, fill="black")
        draw.text((100, 300), f"HUJJAT {index} / 12345", font=font, fill="black")
        draw.text((100, 450), "PDF va Word sinovi", font=font, fill="black")
        image.save(path)
        paths.append(path)
    images_to_pdf(paths, output / "rasmlar.pdf", settings.max_image_pixels)
    images_to_word(paths, output / "rasmlar.docx", settings.max_image_pixels)
    ocr = ocr_available(settings)
    if ocr:
        images_to_text_word(paths, output / "tahrirlanadigan-matn.docx", settings)
    video_results = []
    if settings.ffmpeg and settings.ffprobe:
        source = output / "asl-720p.mp4"
        await run_process(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=10",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ],
            30,
        )
        down = output / "kichraytirilgan-144p.mp4"
        for source_path, target, result_path in [
            (source, 144, down),
            (source, 360, output / "kichraytirilgan-360p.mp4"),
            (source, 480, output / "kichraytirilgan-480p.mp4"),
        ]:
            result = await convert_video(source_path, result_path, target, settings)
            video_results.append(
                {
                    "file": result_path.name,
                    "width": result.width,
                    "height": result.height,
                    "audio": result.has_audio,
                }
            )
    report = {
        "pdf_pages": 2,
        "word_images": 2,
        "ocr_enabled": ocr,
        "videos": video_results,
        "telegram_live_test": False,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
