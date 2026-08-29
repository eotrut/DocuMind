"""Retrieval-augmented generation: prompt assembly and streaming answers.

The contract with the model is deliberately narrow:

* it sees only numbered source blocks, never the raw corpus;
* it must cite with ``[n]`` markers that map 1:1 onto those blocks;
* it must decline when the sources do not answer the question.

Everything the UI needs to render a citation (document, page, chunk, snippet) is
resolved server-side and sent before the first token, so a marker can be turned
into a popover the moment it appears in the stream.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence

from google.genai import types as genai_types

from app.core.config import settings
from app.core.errors import UpstreamError
from app.schemas import Citation
from app.services.gemini_client import get_client, with_retries
from app.services.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

#: Characters of chunk text shown in the citation popover.
SNIPPET_CHARS = 700
#: How many previous turns are replayed to the model for follow-up questions.
HISTORY_TURNS = 6
#: Matches the [1] / [2] markers the model is told to emit.
CITATION_MARKER_RE = re.compile(r"\[(\d{1,2})\]")

SYSTEM_PROMPT = """You are DocuMind, a precise research assistant that answers \
questions strictly from a user's uploaded documents.

Rules you must follow:
1. Use ONLY the numbered SOURCES provided in the user message. Never rely on \
outside knowledge, and never guess.
2. Cite every factual claim with a bracketed marker matching the source you used, \
for example: "Revenue grew 14% year over year [2]." Place the marker immediately \
after the claim it supports.
3. You may cite several sources for one claim, like [1][3]. Only ever use marker \
numbers that appear in the SOURCES list.
4. If the sources do not contain the answer, say so plainly - for example "I could \
not find that in your documents." - and do not invent a citation.
5. Be concise and factual. Prefer short paragraphs or bullet points. Quote the \
document directly when the exact wording matters.
6. Answer in the same language as the question."""

NO_CONTEXT_MESSAGE = (
    "I could not find anything relevant in your documents. Try rephrasing the "
    "question, or upload a document that covers this topic."
)


def build_citations(chunks: Sequence[RetrievedChunk]) -> list[Citation]:
    """Attach 1-based markers to retrieved chunks, in ranked order."""
    return [
        Citation(
            marker=index,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            score=round(chunk.score, 4),
            snippet=_snippet(chunk.content),
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _snippet(content: str) -> str:
    if len(content) <= SNIPPET_CHARS:
        return content
    # Cut on a word boundary so the popover never ends mid-word.
    cut = content[:SNIPPET_CHARS].rsplit(" ", 1)[0]
    return f"{cut}…"


def build_prompt(question: str, citations: Sequence[Citation]) -> str:
    """Render the SOURCES block plus the question into a single user turn."""
    blocks = [
        f"[{c.marker}] {c.filename} (page {c.page_number})\n{c.snippet}"
        for c in citations
    ]
    sources = "\n\n".join(blocks)
    return f"SOURCES\n=======\n{sources}\n\nQUESTION\n========\n{question}"


def used_markers(answer: str, citations: Sequence[Citation]) -> list[int]:
    """Markers the model actually cited, filtered to ones we supplied.

    The model occasionally emits a marker beyond the source list; dropping those
    keeps the UI from rendering a citation chip that resolves to nothing.
    """
    valid = {c.marker for c in citations}
    seen: list[int] = []
    for match in CITATION_MARKER_RE.finditer(answer):
        marker = int(match.group(1))
        if marker in valid and marker not in seen:
            seen.append(marker)
    return seen


def _history_contents(
    history: Sequence[tuple[str, str]],
) -> list[genai_types.Content]:
    """Convert stored (role, content) pairs into Gemini turns.

    Citation markers are stripped from replayed assistant turns: their numbering
    belonged to a previous retrieval, and leaving them in encourages the model to
    reuse stale marker numbers in the new answer.
    """
    contents: list[genai_types.Content] = []
    for role, content in history[-HISTORY_TURNS:]:
        if not content.strip():
            continue
        is_assistant = role == "assistant"
        text = CITATION_MARKER_RE.sub("", content).strip() if is_assistant else content
        if not text:
            continue
        contents.append(
            genai_types.Content(
                role="model" if is_assistant else "user",
                parts=[genai_types.Part.from_text(text=text)],
            )
        )
    return contents


async def stream_answer(
    question: str,
    citations: Sequence[Citation],
    history: Sequence[tuple[str, str]] = (),
) -> AsyncIterator[str]:
    """Yield answer text incrementally from Gemini.

    Connection setup is retried with backoff (free-tier 429s are common); once
    tokens are flowing a mid-stream failure is surfaced as an upstream error
    rather than silently truncating the answer.
    """
    client = get_client()
    contents = _history_contents(history)
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=build_prompt(question, citations))],
        )
    )
    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,  # low: this is extraction, not creative writing
        max_output_tokens=1600,
        # No tools are exposed, so the SDK's function-calling loop is dead weight.
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    )

    async def open_stream():
        return await client.aio.models.generate_content_stream(
            model=settings.gemini_model, contents=contents, config=config
        )

    stream = await with_retries(open_stream, what="Gemini generation")

    try:
        async for event in stream:
            text = getattr(event, "text", None)
            if text:
                yield text
    except Exception as exc:  # noqa: BLE001 - network drop mid-stream
        logger.exception("Gemini stream failed partway through")
        raise UpstreamError("The answer stream was interrupted.") from exc
