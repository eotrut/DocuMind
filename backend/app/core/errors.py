"""Domain exceptions mapped to HTTP responses in ``app.main``."""

from __future__ import annotations


class DocuMindError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    message = "Something went wrong."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class RateLimitedError(DocuMindError):
    """Gemini free-tier quota exhausted after all retries."""

    status_code = 429
    message = (
        "The Gemini free tier is rate-limited right now. Please wait a moment "
        "and try again."
    )

    def __init__(self, message: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamError(DocuMindError):
    """Gemini returned an error we cannot recover from."""

    status_code = 502
    message = "The AI service is temporarily unavailable. Please try again."


class ConfigurationError(DocuMindError):
    """The deployment is missing configuration it needs to serve the request."""

    status_code = 503
    message = "The server is not configured correctly."
