"""Tests for the streaming half of the RAG service.

The Gemini SDK connects lazily: `generate_content_stream` returns before any
network I/O happens, so a rate limit or a DNS failure only surfaces when the
first chunk is awaited. These tests pin the resulting behaviour - a failure at
that point is a *connection* failure (retried, and reported as a rate limit when
it is one), while a failure after tokens have flowed is a truncated answer.
"""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from app.core.errors import RateLimitedError, UpstreamError
from app.schemas import Citation
from app.services import gemini_client, rag


def _api_error(code: int, message: str) -> genai_errors.APIError:
    error = genai_errors.APIError.__new__(genai_errors.APIError)
    Exception.__init__(error, message)
    error.code = code
    error.message = message
    return error


class _Event:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeStream:
    """Yields texts, optionally raising at a chosen position."""

    def __init__(self, texts: list[str], raise_at: int | None, error: Exception | None):
        self._texts = texts
        self._raise_at = raise_at
        self._error = error
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_at is not None and self._index == self._raise_at:
            assert self._error is not None
            raise self._error
        if self._index >= len(self._texts):
            raise StopAsyncIteration
        event = _Event(self._texts[self._index])
        self._index += 1
        return event


class _FakeModels:
    def __init__(self, streams: list[_FakeStream]) -> None:
        self._streams = streams
        self.calls = 0

    async def generate_content_stream(self, **_: object) -> _FakeStream:
        stream = self._streams[min(self.calls, len(self._streams) - 1)]
        self.calls += 1
        return stream


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = type("Aio", (), {"models": models})()


CITATIONS = [
    Citation(
        marker=1,
        chunk_id="c1",
        document_id="d1",
        filename="report.pdf",
        page_number=2,
        chunk_index=0,
        score=0.8,
        snippet="Revenue was 4.2 million euros.",
    )
]


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(gemini_client.asyncio, "sleep", fake_sleep)


def _install(monkeypatch, streams: list[_FakeStream]) -> _FakeModels:
    models = _FakeModels(streams)
    monkeypatch.setattr(rag, "get_client", lambda: _FakeClient(models))
    return models


async def _collect(**kwargs) -> str:
    return "".join([piece async for piece in rag.stream_answer(**kwargs)])


async def test_a_healthy_stream_is_yielded_in_order(monkeypatch):
    _install(monkeypatch, [_FakeStream(["Revenue was ", "4.2m [1]."], None, None)])

    answer = await _collect(question="How much?", citations=CITATIONS)

    assert answer == "Revenue was 4.2m [1]."


async def test_a_rate_limit_on_the_first_chunk_is_retried(monkeypatch):
    models = _install(
        monkeypatch,
        [
            _FakeStream([], 0, _api_error(429, "RESOURCE_EXHAUSTED")),
            _FakeStream(["Recovered [1]."], None, None),
        ],
    )

    answer = await _collect(question="How much?", citations=CITATIONS)

    assert answer == "Recovered [1]."
    assert models.calls == 2, "expected the stream to be reopened, not resumed"


async def test_a_persistent_rate_limit_on_connect_surfaces_as_429(monkeypatch):
    _install(monkeypatch, [_FakeStream([], 0, _api_error(429, "RESOURCE_EXHAUSTED"))])

    with pytest.raises(RateLimitedError):
        await _collect(question="How much?", citations=CITATIONS)


async def test_a_connection_error_on_the_first_chunk_is_retried(monkeypatch):
    models = _install(
        monkeypatch,
        [
            _FakeStream([], 0, ConnectionError("getaddrinfo failed")),
            _FakeStream(["Back online [1]."], None, None),
        ],
    )

    assert await _collect(question="How much?", citations=CITATIONS) == "Back online [1]."
    assert models.calls == 2


async def test_a_failure_after_the_first_token_is_a_truncated_answer(monkeypatch):
    models = _install(
        monkeypatch,
        [_FakeStream(["Partial answer "], 1, ConnectionError("dropped"))],
    )

    with pytest.raises(UpstreamError, match="interrupted"):
        await _collect(question="How much?", citations=CITATIONS)

    assert models.calls == 1, "a mid-stream drop must not silently restart the answer"


async def test_an_empty_stream_yields_nothing_without_raising(monkeypatch):
    _install(monkeypatch, [_FakeStream([], None, None)])

    assert await _collect(question="How much?", citations=CITATIONS) == ""
