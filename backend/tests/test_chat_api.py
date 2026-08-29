"""End-to-end tests for the streaming chat endpoint and conversation history.

Gemini is never called: ``rag.stream_answer`` is replaced with a generator that
emits a fixed, citation-bearing answer. Retrieval, citation resolution, SSE
framing and persistence are all exercised for real.
"""

from __future__ import annotations

import json

import pytest

from app.services import rag
from tests.factories import build_sample_pdf

ANSWER_CHUNKS = ["Revenue was 4.2 million euros ", "in FY2024 [1]."]


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Turn a raw event-stream body into (event, payload) pairs."""
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name is not None:
            events.append((name, data or {}))
    return events


@pytest.fixture(autouse=True)
def stub_gemini(monkeypatch):
    """Replace the Gemini stream with a deterministic local generator."""

    async def fake_stream(question, citations, history=()):
        for piece in ANSWER_CHUNKS:
            yield piece

    monkeypatch.setattr(rag, "stream_answer", fake_stream)


@pytest.fixture
async def client_with_document(auth_client):
    response = await auth_client.post(
        "/api/documents",
        files={"files": ("annual-report.pdf", build_sample_pdf(), "application/pdf")},
    )
    assert response.json()["results"][0]["ok"] is True
    return auth_client


async def test_chat_streams_meta_sources_tokens_and_done(client_with_document):
    response = await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue in 2024?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[1] == "sources"
    assert "token" in names
    assert names[-1] == "done"

    payloads = dict(events[:2])
    assert payloads["meta"]["conversation_id"]
    citations = payloads["sources"]["citations"]
    assert citations, "expected retrieved sources"
    assert citations[0]["marker"] == 1
    assert citations[0]["filename"] == "annual-report.pdf"
    assert citations[0]["snippet"]

    answer = "".join(p["text"] for name, p in events if name == "token")
    assert answer == "".join(ANSWER_CHUNKS)


async def test_done_event_reports_only_the_markers_the_model_cited(
    client_with_document,
):
    response = await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue in 2024?"}
    )
    done = dict(parse_sse(response.text))["done"]

    assert done["used_markers"] == [1]
    assert done["message_id"]


async def test_the_exchange_is_persisted_with_its_citations(client_with_document):
    stream = await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue in 2024?"}
    )
    conversation_id = dict(parse_sse(stream.text))["meta"]["conversation_id"]

    response = await client_with_document.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "What was revenue in 2024?"
    assert messages[1]["content"] == "".join(ANSWER_CHUNKS)

    citation = messages[1]["citations"][0]
    assert citation["marker"] == 1
    assert citation["filename"] == "annual-report.pdf"
    assert citation["page_number"] >= 1


async def test_a_follow_up_reuses_the_same_conversation(client_with_document):
    first = await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue?"}
    )
    conversation_id = dict(parse_sse(first.text))["meta"]["conversation_id"]

    second = await client_with_document.post(
        "/api/chat/stream",
        json={"message": "And headcount?", "conversation_id": conversation_id},
    )

    assert dict(parse_sse(second.text))["meta"]["conversation_id"] == conversation_id
    detail = await client_with_document.get(f"/api/conversations/{conversation_id}")
    assert len(detail.json()["messages"]) == 4


async def test_conversations_are_listed_with_a_generated_title(client_with_document):
    await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue in 2024?"}
    )
    response = await client_with_document.get("/api/conversations")

    assert response.status_code == 200
    assert response.json()[0]["title"] == "What was revenue in 2024?"


async def test_asking_with_no_documents_answers_gracefully(auth_client):
    response = await auth_client.post(
        "/api/chat/stream", json={"message": "What was revenue?"}
    )
    events = dict(parse_sse(response.text))

    assert events["sources"]["citations"] == []
    answer = "".join(
        p["text"] for name, p in parse_sse(response.text) if name == "token"
    )
    assert answer == rag.NO_CONTEXT_MESSAGE
    assert events["done"]["used_markers"] == []


async def test_document_filter_narrows_retrieval(client_with_document):
    documents = (await client_with_document.get("/api/documents")).json()

    response = await client_with_document.post(
        "/api/chat/stream",
        json={"message": "revenue", "document_ids": ["a-document-that-does-not-exist"]},
    )
    assert dict(parse_sse(response.text))["sources"]["citations"] == []

    response = await client_with_document.post(
        "/api/chat/stream",
        json={"message": "revenue", "document_ids": [documents[0]["id"]]},
    )
    assert dict(parse_sse(response.text))["sources"]["citations"]


async def test_an_empty_message_is_rejected(client_with_document):
    response = await client_with_document.post(
        "/api/chat/stream", json={"message": "   "}
    )
    assert response.status_code == 422


async def test_chat_requires_authentication(client):
    response = await client.post("/api/chat/stream", json={"message": "hello"})
    assert response.status_code == 401


async def test_an_unknown_conversation_id_is_a_404(client_with_document):
    response = await client_with_document.post(
        "/api/chat/stream",
        json={"message": "hello", "conversation_id": "does-not-exist"},
    )
    assert response.status_code == 404


async def test_a_conversation_can_be_deleted(client_with_document):
    stream = await client_with_document.post(
        "/api/chat/stream", json={"message": "What was revenue?"}
    )
    conversation_id = dict(parse_sse(stream.text))["meta"]["conversation_id"]

    assert (
        await client_with_document.delete(f"/api/conversations/{conversation_id}")
    ).status_code == 204
    assert (await client_with_document.get("/api/conversations")).json() == []
    assert (
        await client_with_document.get(f"/api/conversations/{conversation_id}")
    ).status_code == 404
