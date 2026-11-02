from dataclasses import replace

import pytest

from botapp.config import MB, Settings


def test_token_and_webhook_secret_are_not_in_repr():
    settings = Settings(token="private-token", webhook_secret="private-secret")
    assert "private-token" not in repr(settings)
    assert "private-secret" not in repr(settings)


def test_cloud_limits_cannot_be_accidentally_increased(settings):
    with pytest.raises(ValueError, match="TELEGRAM_API_URL"):
        replace(settings, max_input_bytes=21 * MB).validate()
    replace(
        settings, max_input_bytes=100 * MB, api_url="http://127.0.0.1:8081", local_mode=True
    ).validate()


def test_webhook_requires_https_and_secret(settings):
    with pytest.raises(ValueError, match="HTTPS"):
        replace(settings, mode="webhook", webhook_url="http://example.com").validate()
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        replace(settings, mode="webhook", webhook_url="https://example.com").validate()
    replace(
        settings, mode="webhook", webhook_url="https://example.com", webhook_secret="a" * 32
    ).validate()


def test_large_limits_require_local_mode_and_safe_ceiling(settings):
    with pytest.raises(ValueError, match="TELEGRAM_LOCAL_MODE"):
        replace(settings, max_input_bytes=100 * MB, api_url="http://127.0.0.1:8081").validate()
    replace(
        settings,
        max_input_bytes=1900 * MB,
        max_output_bytes=1900 * MB,
        api_url="http://127.0.0.1:8081",
        local_mode=True,
    ).validate()
    with pytest.raises(ValueError, match="1900"):
        replace(
            settings, max_input_bytes=2000 * MB, api_url="http://127.0.0.1:8081", local_mode=True
        ).validate()
