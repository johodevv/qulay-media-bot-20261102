"""Bounded benchmark: same 1080p source, seven variants, two rounds, no user files."""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from botapp.config import ROOT, Settings  # noqa: E402
from botapp.media import probe_video, run_process  # noqa: E402


async def main():
    settings = Settings.from_env(require_token=False)
    root = ROOT / "test-results" / "benchmark"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source-1080p.mp4"
    await run_process(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        60,
    )
    variants = [
        ("baseline", "lanczos", "veryfast", 23, 1, 30),
        ("preserve-fps", "lanczos", "veryfast", 23, 1, 24),
        ("crf28", "lanczos", "veryfast", 28, 1, 24),
        ("bilinear", "bilinear", "veryfast", 28, 1, 24),
        ("fast-bilinear", "fast_bilinear", "veryfast", 28, 1, 24),
        ("two-threads", "fast_bilinear", "veryfast", 28, 2, 24),
    ]
    results = []
    for round_number in range(3):
        for name, scaler, preset, crf, threads, fps in variants:
            output = root / f"{name}.mp4"
            start = time.perf_counter()
            await run_process(
                [
                    settings.ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-threads",
                    str(threads),
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    f"scale=640:360:flags={scaler},setsar=1,fps={fps}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    preset,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    str(threads),
                    "-filter_threads",
                    "1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                60,
            )
            info = await probe_video(output, settings)
            seconds = round(time.perf_counter() - start, 3)
            valid = (info.width, info.height) == (640, 360)
            valid &= info.has_audio and abs(info.duration - 8) < 0.15
            valid &= output.stat().st_size < source.stat().st_size
            result = next((item for item in results if item["variant"] == name), None)
            if result is None:
                result = {"variant": name, "seconds": [], "correct": True}
                results.append(result)
            result["seconds"].append(seconds)
            result["median"] = statistics.median(result["seconds"])
            result["bytes"] = output.stat().st_size
            result["correct"] &= bool(valid)
            print(
                json.dumps(
                    {
                        "round": round_number + 1,
                        "variant": name,
                        "seconds": seconds,
                        "correct": bool(valid),
                    }
                ),
                flush=True,
            )
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
