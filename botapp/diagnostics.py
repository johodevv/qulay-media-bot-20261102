"""Authenticated, fixed-input OCR comparison; never processes user documents."""

import asyncio
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from botapp.config import Settings
from botapp.document_layout import font_path


def make_sample(path: Path):
    image = Image.new("RGB", (1100, 550), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path()), 36)
    for row in range(8):
        draw.text((40, 20 + row * 60), f"HUJJAT {row + 1} DOCUMENT 12345", fill="black", font=font)
    image.save(path)
    image.close()


async def benchmark_ocr(settings: Settings, busy) -> dict:
    with tempfile.TemporaryDirectory(prefix="qb-benchmark-") as folder:
        source = Path(folder) / "sample.png"
        await asyncio.to_thread(make_sample, source)
        results, expected = [], None
        for threads in (4, 1):
            if busy():
                return {"status": "deferred", "reason": "bot_busy"}
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(
                settings.tesseract,
                str(source),
                "stdout",
                "-l",
                settings.ocr_languages,
                "--psm",
                "3",
                env=dict(os.environ, OMP_THREAD_LIMIT=str(threads)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output = asyncio.create_task(process.communicate())
            try:
                while not output.done():
                    if busy():
                        return {"status": "deferred", "reason": "bot_busy"}
                    if time.monotonic() - started > 60:
                        return {"status": "timeout"}
                    await asyncio.wait({output}, timeout=0.25)
                stdout, _ = await output
                if process.returncode or stdout.count(b"12345") != 8:
                    return {"status": "incorrect_output"}
                if expected is None:
                    expected = stdout
                results.append(
                    {
                        "omp_threads": threads,
                        "seconds": round(time.monotonic() - started, 3),
                        "same_text": stdout == expected,
                    }
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await asyncio.gather(output, return_exceptions=True)
        return {"status": "ok", "results": results}
