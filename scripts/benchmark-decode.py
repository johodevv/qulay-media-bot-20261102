"""Bounded decode/encode comparison; --confirm-fast repeats only baseline and winner."""

import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from botapp.config import ROOT, Settings  # noqa: E402
from botapp.media import probe_video  # noqa: E402


async def main():
    s = Settings.from_env(False)
    source = ROOT / "test-results/benchmark/source-1080p.mp4"
    root = ROOT / "test-results/decode-benchmark"
    root.mkdir(parents=True, exist_ok=True)
    variants = [
        ("baseline", [], "veryfast", 28),
        ("fast-15fps", [], "veryfast", 28),
        ("skip-deblock", ["-skip_loop_filter", "all"], "veryfast", 28),
        ("superfast", ["-skip_loop_filter", "all"], "superfast", 30),
        ("ultrafast", ["-skip_loop_filter", "all"], "ultrafast", 32),
    ]
    rows = []
    confirm = "--confirm-fast" in sys.argv
    if confirm:
        variants = variants[:2]
    for height in (720,) if confirm else (360, 720):
        for run in range(3):
            for name, options, preset, crf in variants:
                target = 360 if confirm and name == "fast-15fps" else height
                output = root / f"{name}-{target}.mp4"
                fps = 15 if name == "fast-15fps" else 24
                args = [
                    s.ffmpeg,
                    "-hide_banner",
                    "-benchmark",
                    "-y",
                    "-threads",
                    "2",
                    *options,
                    "-i",
                    str(source),
                    "-t",
                    "8",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    f"scale={target * 16 // 9}:{target}:flags=fast_bilinear,setsar=1,fps={fps}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    preset,
                    "-crf",
                    str(crf),
                    "-threads",
                    "2",
                    "-filter_threads",
                    "1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96000",
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
                started = time.perf_counter()
                proc = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                )
                async with asyncio.timeout(60):
                    _, stderr = await proc.communicate()
                assert proc.returncode == 0
                wall = time.perf_counter() - started
                match = re.search(rb"utime=([\d.]+)s stime=([\d.]+)s", stderr)
                cpu = sum(float(value) for value in match.groups())
                info = await probe_video(output, s)
                assert info.height == target and info.has_audio and abs(info.duration - 8) < 0.15
                assert output.stat().st_size < source.stat().st_size
                row = next((r for r in rows if r["name"] == name and r["height"] == target), None)
                if row is None:
                    row = {"name": name, "height": target, "wall": [], "cpu": []}
                    rows.append(row)
                row["wall"].append(round(wall, 3))
                row["cpu"].append(cpu)
                row["bytes"] = output.stat().st_size
                print(height, run, name, round(wall, 3), round(cpu, 3), flush=True)
    for row in rows:
        row["median_wall"] = statistics.median(row["wall"])
        row["median_cpu"] = statistics.median(row["cpu"])
    filename = "confirmation.json" if confirm else "results.json"
    (root / filename).write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
