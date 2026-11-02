"""Bounded speed search: 3 x 4 video variants and two equivalent ink estimators."""

import ast
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402

from botapp import document_layout  # noqa: E402
from botapp.config import ROOT, Settings  # noqa: E402
from botapp.document_layout import font_path, ink_and_background  # noqa: E402
from botapp.media import probe_video, run_process  # noqa: E402


def baseline_ink():
    # Immutable accepted baseline; load only this project's single named function.
    source = subprocess.check_output(
        ["git", "show", "9be83fecf04e4eda4815b587fcf505abeeeeb7ea:botapp/document_layout.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "ink_and_background"
    )
    namespace = {"Image": Image, "statistics": statistics}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "accepted-baseline", "exec"),
        namespace,
    )
    return namespace["ink_and_background"]


def ocr_benchmark():
    from docx import Document
    from PIL import ImageDraw, ImageFont

    root = ROOT / "test-results" / "speed"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "document.png"
    image = Image.new("RGB", (1300, 1500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path()), 32)
    for row in range(28):
        draw.text(
            (50, 30 + row * 48),
            f"DOCUMENT {row + 1:02d} HUJJAT 12345 EXAMPLE",
            font=font,
            fill=(30, 30, 30),
        )
    image.save(source)
    image.close()
    settings = Settings.from_env(False)
    expected, results = None, []
    for name, function in [("baseline", baseline_ink()), ("fast", ink_and_background)]:
        times = []
        document_layout.ink_and_background = function
        for _ in range(3):
            output = root / f"ocr-{name}.docx"
            start = time.perf_counter()
            document_layout.images_to_layout_word([source], output, settings)
            times.append(time.perf_counter() - start)
            text = Document(output).element.xpath("//w:txbxContent//w:t/text()")
            if expected is None:
                expected = text
            assert text == expected and any("12345" in value for value in text)
        results.append({"variant": name, "median_s": statistics.median(times), "samples": times})
        print(json.dumps(results[-1]), flush=True)
    document_layout.ink_and_background = ink_and_background
    (root / "ocr.json").write_text(json.dumps(results, indent=2))


async def main():
    s = Settings.from_env(False)
    root = ROOT / "test-results" / "speed"
    root.mkdir(parents=True, exist_ok=True)
    source = ROOT / "test-results" / "benchmark" / "source-1080p.mp4"
    if not source.exists():
        raise RuntimeError("Run benchmark-video.py once to create the synthetic source")
    results = []
    variants = [
        ("veryfast-2", "veryfast", 2),
        ("veryfast-1", "veryfast", 1),
        ("superfast-1", "superfast", 1),
        ("ultrafast-1", "ultrafast", 1),
    ]
    for run in range(3):
        for name, preset, threads in variants:
            output = root / f"{name}.mp4"
            started = time.perf_counter()
            await run_process(
                [
                    s.ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-threads",
                    str(threads),
                    "-i",
                    str(source),
                    "-t",
                    "8",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    "scale=640:360:flags=fast_bilinear,setsar=1,fps=24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    preset,
                    "-crf",
                    "28",
                    "-threads",
                    str(threads),
                    "-filter_threads",
                    "1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96000",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                60,
            )
            elapsed = time.perf_counter() - started
            info = await probe_video(output, s)
            valid = (info.width, info.height) == (640, 360) and info.has_audio
            valid &= abs(info.duration - 8) < 0.15 and output.stat().st_size < source.stat().st_size
            row = next((row for row in results if row["variant"] == name), None)
            if row is None:
                row = {"variant": name, "seconds": [], "correct": True}
                results.append(row)
            row["seconds"].append(round(elapsed, 3))
            row["correct"] &= valid
            row["bytes"] = output.stat().st_size
            row["median"] = statistics.median(row["seconds"])
            print(name, run + 1, round(elapsed, 3), flush=True)
    for row in results:
        stats = root / f"ssim-{row['variant']}.txt"
        await run_process(
            [
                s.ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                str(root / "veryfast-2.mp4"),
                "-i",
                str(root / f"{row['variant']}.mp4"),
                "-lavfi",
                f"ssim=stats_file={stats.name}",
                "-an",
                "-f",
                "null",
                "-",
            ],
            60,
        )
        # FFmpeg writes relative stats paths in cwd, avoiding Windows drive escaping.
        local = Path(stats.name)
        lines = local.read_text().splitlines()
        row["ssim"] = statistics.mean(float(line.split("All:")[1].split()[0]) for line in lines)
        local.replace(stats)
    (root / "video.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2), flush=True)
    from PIL import ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path()), 36)
    cases = []
    for ink in [(0, 0, 0), (100, 100, 100), (25, 90, 200), (200, 25, 25)]:
        image = Image.new("RGB", (350, 100), (240, 240, 240))
        ImageDraw.Draw(image).text((20, 20), "HUJJAT 12345", font=font, fill=ink)
        cases.append(image)
    ink_results = []
    old_ink = baseline_ink()
    baseline = [old_ink(img, (20, 25, 270, 35)) for img in cases]
    for variant, function in [("baseline", old_ink), ("fast", ink_and_background)]:
        times = []
        for _ in range(3):
            started = time.perf_counter()
            for _ in range(100):
                for img in cases:
                    function(img, (20, 25, 270, 35))
            times.append(time.perf_counter() - started)
        actual = [function(img, (20, 25, 270, 35)) for img in cases]
        error = max(
            abs(a - b)
            for old, new in zip(baseline, actual, strict=True)
            for a, b in zip(old[0], new[0], strict=True)
        )
        ink_results.append(
            {
                "variant": variant,
                "median": statistics.median(times),
                "max_color_delta": error,
            }
        )
    (root / "ink.json").write_text(json.dumps(ink_results, indent=2))
    print(json.dumps(ink_results, indent=2), flush=True)


if __name__ == "__main__":
    if "--ocr-only" in sys.argv:
        ocr_benchmark()
    else:
        asyncio.run(main())
