from dataclasses import replace

import pytest

from botapp.config import Settings


@pytest.fixture
def settings():
    return replace(Settings.from_env(require_token=False), token="123456:" + "a" * 35)


@pytest.fixture
def ffmpeg_settings(settings):
    if not settings.ffmpeg or not settings.ffprobe:
        pytest.skip("FFmpeg and FFprobe are required for video integration tests")
    return settings
