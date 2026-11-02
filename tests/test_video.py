import asyncio
from dataclasses import replace

import pytest

from botapp.media import (
    RESOLUTIONS,
    MediaError,
    VideoInfo,
    convert_video,
    probe_video,
    run_process,
    target_dimensions,
)


async def make_video(path, settings, size="256x144", audio=True):
    args = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=10",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    args += ["-t", "0.6", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    await run_process([*args, str(path)], 30)


@pytest.mark.parametrize(
    "resolution,size",
    [
        (144, "640x360"),
        (360, "1280x720"),
        (480, "1280x720"),
        (720, "1920x1080"),
        (1080, "2560x1440"),
        (1440, "3840x2160"),
        (2160, "4096x2304"),
    ],
)
async def test_real_video_downscale_at_all_resolutions(ffmpeg_settings, tmp_path, resolution, size):
    source, output = tmp_path / "input.mp4", tmp_path / "output.mp4"
    await make_video(source, ffmpeg_settings, size)
    progress = []
    result = await convert_video(
        source, output, resolution, ffmpeg_settings, on_progress=progress.append
    )
    assert result.height == resolution
    assert result.width % 2 == 0
    assert result.width / result.height == pytest.approx(16 / 9, abs=0.015)
    assert result.has_audio
    assert result.duration == pytest.approx(0.6, abs=0.15)
    assert output.stat().st_size < ffmpeg_settings.max_output_bytes
    assert output.stat().st_size < source.stat().st_size
    assert result.fps <= 10.1
    assert progress and all(0 <= value <= 0.99 for value in progress)


async def test_downscale_portrait_without_audio(ffmpeg_settings, tmp_path):
    source, output = tmp_path / "portrait.mp4", tmp_path / "result.mp4"
    await make_video(source, ffmpeg_settings, "720x1280", audio=False)
    result = await convert_video(source, output, 144, ffmpeg_settings)
    assert (result.width, result.height) == (144, 256)
    assert not result.has_audio


@pytest.mark.parametrize("size,expected", [("1920x1080", (640, 360)), ("256x144", (256, 144))])
async def test_fast_mode_caps_resolution_and_frames_without_upscaling(
    ffmpeg_settings, tmp_path, size, expected
):
    source, output = tmp_path / "fast-source.mp4", tmp_path / "fast.mp4"
    await make_video(source, ffmpeg_settings, size)
    info = await convert_video(source, output, 0, ffmpeg_settings, fast=True)
    assert (info.width, info.height) == expected
    assert info.fps <= 15 and info.has_audio
    assert info.duration == pytest.approx(0.6, abs=0.15)
    assert output.stat().st_size < source.stat().st_size


async def test_rotation_metadata_preserves_display_orientation(ffmpeg_settings, tmp_path):
    source, rotated = tmp_path / "input.mp4", tmp_path / "rotated.mp4"
    await make_video(source, ffmpeg_settings, "1280x720")
    await run_process(
        [
            ffmpeg_settings.ffmpeg,
            "-v",
            "error",
            "-y",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(source),
            "-c",
            "copy",
            str(rotated),
        ],
        30,
    )
    original_info = await probe_video(rotated, ffmpeg_settings)
    assert original_info.height > original_info.width
    result = await convert_video(rotated, tmp_path / "out.mp4", 360, ffmpeg_settings)
    assert (result.width, result.height) == (360, 640)


async def test_invalid_video_and_duration_limit(ffmpeg_settings, tmp_path):
    source = tmp_path / "fake.mp4"
    source.write_text("invalid", encoding="utf-8")
    with pytest.raises(MediaError):
        await probe_video(source, ffmpeg_settings)
    await make_video(source, ffmpeg_settings)
    with pytest.raises(MediaError, match="uzun"):
        await probe_video(source, replace(ffmpeg_settings, max_video_seconds=0.1))


async def test_playlists_cannot_open_other_local_files(ffmpeg_settings, tmp_path):
    source = tmp_path / "real.mp4"
    await make_video(source, ffmpeg_settings)
    playlist = tmp_path / "pretend-video.mp4"
    playlist.write_text(f"ffconcat version 1.0\nfile '{source.as_posix()}'\n", encoding="utf-8")
    with pytest.raises(MediaError):
        await probe_video(playlist, ffmpeg_settings)


async def test_process_timeout_is_bounded(ffmpeg_settings):
    with pytest.raises(TimeoutError):
        await run_process(
            [
                ffmpeg_settings.ffmpeg,
                "-v",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=16x16",
                "-f",
                "null",
                "-",
            ],
            0.5,
        )


def test_ultrawide_dimensions_are_bounded_and_even():
    assert target_dimensions(VideoInfo(6000, 1000, 1, False), 1080) == (1920, 320)
    with pytest.raises(MediaError):
        target_dimensions(VideoInfo(100, 100, 1, False), 9999)


def test_upscaling_and_same_size_are_rejected():
    for resolution in RESOLUTIONS:
        with pytest.raises(MediaError, match="kichik emas"):
            target_dimensions(VideoInfo(256, 144, 1, False), resolution)
    assert target_dimensions(VideoInfo(256, 144, 1, False), 0) == (256, 144)


async def test_cancellation_stops_child_process():
    # A real child process is terminated when its awaiting task is cancelled.
    import sys

    task = asyncio.create_task(
        run_process([sys.executable, "-c", "import time; time.sleep(30)"], 60)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
