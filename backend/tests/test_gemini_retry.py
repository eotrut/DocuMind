"""Tests for the Gemini free-tier retry / backoff policy."""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from app.core.errors import ConfigurationError, RateLimitedError, UpstreamError
from app.services import gemini_client
from app.services.gemini_client import get_client, reset_client, with_retries


def _api_error(code: int, message: str = "boom") -> genai_errors.APIError:
    error = genai_errors.APIError.__new__(genai_errors.APIError)
    Exception.__init__(error, message)
    error.code = code
    error.message = message
    return error


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Keep the suite fast: backoff delays are asserted, never waited out."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(gemini_client.asyncio, "sleep", fake_sleep)
    return slept


async def test_a_successful_call_is_not_retried(no_sleeping):
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return "done"

    assert await with_retries(operation) == "done"
    assert calls == 1
    assert no_sleeping == []


async def test_a_transient_error_is_retried_and_then_succeeds(no_sleeping):
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _api_error(503, "UNAVAILABLE")
        return "recovered"

    assert await with_retries(operation) == "recovered"
    assert calls == 3
    assert len(no_sleeping) == 2


async def test_persistent_rate_limiting_raises_a_user_facing_429(no_sleeping):
    async def operation():
        raise _api_error(429, "RESOURCE_EXHAUSTED: quota exceeded")

    with pytest.raises(RateLimitedError) as exc_info:
        await with_retries(operation)

    assert exc_info.value.status_code == 429
    assert "rate-limited" in exc_info.value.message
    assert len(no_sleeping) == gemini_client.MAX_ATTEMPTS - 1


async def test_backoff_grows_between_attempts(no_sleeping):
    async def operation():
        raise _api_error(429, "RESOURCE_EXHAUSTED")

    with pytest.raises(RateLimitedError):
        await with_retries(operation)

    assert no_sleeping == sorted(no_sleeping)
    assert all(delay <= gemini_client.MAX_DELAY_SECONDS + 1 for delay in no_sleeping)


async def test_the_servers_own_retry_delay_hint_is_honoured(no_sleeping):
    async def operation():
        raise _api_error(429, "RESOURCE_EXHAUSTED, 'retryDelay': '7s'")

    with pytest.raises(RateLimitedError) as exc_info:
        await with_retries(operation)

    assert exc_info.value.retry_after == 7
    assert all(7 <= delay < 8 for delay in no_sleeping)


async def test_a_client_error_fails_immediately_without_retrying(no_sleeping):
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise _api_error(400, "INVALID_ARGUMENT")

    with pytest.raises(UpstreamError):
        await with_retries(operation)

    assert calls == 1
    assert no_sleeping == []


def test_a_missing_api_key_is_a_configuration_error(monkeypatch):
    reset_client()
    monkeypatch.setattr(gemini_client.settings, "gemini_api_key", "")

    with pytest.raises(ConfigurationError, match="aistudio.google.com"):
        get_client()

    reset_client()
