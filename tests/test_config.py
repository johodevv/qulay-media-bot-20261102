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
    replace(settings, max_input_bytes=100 * MB, api_url="http://127.0.0.1:8081").validate()


def test_webhook_requires_https_and_secret(settings):
    with pytest.raises(ValueError, match="HTTPS"):
        replace(settings, mode="webhook", webhook_url="http://example.com").validate()
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        replace(settings, mode="webhook", webhook_url="https://example.com").validate()
    replace(
        settings, mode="webhook", webhook_url="https://example.com", webhook_secret="a" * 32
    ).validate()
