"""Shared Google Gemini client, retry policy and error translation.

Free-tier quotas are low (requests-per-minute *and* requests-per-day), so every
call goes through :func:`with_retries`, which backs off exponentially on 429 /
503 and finally raises a :class:`RateLimitedError` the API turns into a clean
HTTP 429 for the UI.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from google import genai
from google.genai import errors as genai_errors

from app.core.config import settings
from app.core.errors import ConfigurationError, RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 1.5
MAX_DELAY_SECONDS = 30.0

_client: genai.Client | None = None


def get_client() -> genai.Client:
    """Lazily build a process-wide Gemini client."""
    global _client
    if not settings.gemini_api_key:
        raise ConfigurationError(
            "GEMINI_API_KEY is not set. Add it to your .env file — get a free "
            "key at https://aistudio.google.com/app/apikey"
        )
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def reset_client() -> None:
    """Drop the cached client (used by tests when settings change)."""
    global _client
    _client = None


def _retry_after_seconds(exc: Exception) -> int | None:
    """Best-effort parse of Google's ``retryDelay: 41s`` hint."""
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)s", str(exc))
    return int(match.group(1)) if match else None


def _is_rate_limit(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "RESOURCE_EXHAUSTED" in str(exc)


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in (500, 502, 503, 504) or "UNAVAILABLE" in str(exc)


async def with_retries(
    operation: Callable[[], Awaitable[T]], *, what: str = "Gemini call"
) -> T:
    """Run ``operation`` with exponential backoff + jitter on 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except genai_errors.APIError as exc:
            last_exc = exc
            retryable = _is_rate_limit(exc) or _is_transient(exc)
            if not retryable:
                logger.warning("%s failed permanently: %s", what, exc)
                raise UpstreamError(f"{what} failed: {exc}") from exc
            if attempt == MAX_ATTEMPTS:
                break
            # Honour the server's own hint when it gives one, else exponential
            # backoff with jitter to avoid a thundering herd of retries.
            hinted = _retry_after_seconds(exc)
            delay = min(
                hinted if hinted is not None else BASE_DELAY_SECONDS * 2 ** (attempt - 1),
                MAX_DELAY_SECONDS,
            )
            delay += random.uniform(0, 0.5)
            logger.info(
                "%s hit a %s; retrying in %.1fs (attempt %d/%d)",
                what,
                "rate limit" if _is_rate_limit(exc) else "transient error",
                delay,
                attempt,
                MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/SDK errors
            last_exc = exc
            if attempt == MAX_ATTEMPTS:
                break
            await asyncio.sleep(
                min(BASE_DELAY_SECONDS * 2 ** (attempt - 1), MAX_DELAY_SECONDS)
            )

    assert last_exc is not None
    if _is_rate_limit(last_exc):
        raise RateLimitedError(retry_after=_retry_after_seconds(last_exc)) from last_exc
    raise UpstreamError(f"{what} failed after {MAX_ATTEMPTS} attempts.") from last_exc


AsyncFn = Callable[..., Awaitable[T]]


def retryable(what: str) -> Callable[[AsyncFn[T]], AsyncFn[T]]:
    """Decorator form of :func:`with_retries`."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await with_retries(lambda: fn(*args, **kwargs), what=what)

        return wrapper

    return decorator
