"""Page-aware text chunking.

Chunking strategy (and why)
---------------------------
1. **Split, don't slice.** Text is first broken into *units* — paragraphs, then
   sentences for over-long paragraphs, then fixed word windows as a last resort.
   Packing whole units means a chunk almost never ends mid-sentence, which
   measurably improves both embedding quality and how quotable a citation is.
2. **Pack across page boundaries.** A running buffer fills to ``chunk_size``
   characters before it is cut. Chunking strictly per page would produce lots of
   tiny, semantically thin chunks on title/reference pages. Each chunk records
   the page its first unit came from, so a citation still points at a real page.
3. **Overlap by whole units.** The tail units of a chunk (up to
   ``chunk_overlap`` characters) are prepended to the next one, so an answer
   that straddles a boundary can still be retrieved by either chunk.
4. **Drop scraps.** Trailing fragments below ``MIN_CHUNK_CHARS`` are merged back
   into the previous chunk rather than stored as near-useless vectors.

Sizes are in characters, not tokens: it keeps ingestion dependency-free and, at
roughly 4 characters per token, ``CHUNK_SIZE=1000`` lands near a comfortable
250-token chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A chunk shorter than this is merged into its predecessor instead of stored.
MIN_CHUNK_CHARS = 120

_PARAGRAPH_RE = re.compile(r"\n\s*\n+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'\u201c])")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class PageText:
    """Raw text extracted from a single PDF page (1-indexed)."""

    page_number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    """A stored, embeddable unit of a document."""

    chunk_index: int
    page_number: int  # page the chunk starts on
    content: str

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class _Unit:
    page_number: int
    text: str
    #: Units carved out of the same source paragraph share a block id, so they
    #: are re-joined with a space rather than a paragraph break.
    block: int


def normalise_whitespace(text: str) -> str:
    """Collapse PDF extraction noise while preserving paragraph structure."""
    text = text.replace("\u00a0", " ").replace("\x00", "")
    # De-hyphenate words broken across a line break: "informa-\ntion" -> "information"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _split_long(text: str, limit: int) -> list[str]:
    """Break an over-long unit into sentences, then word windows if needed."""
    pieces: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        words, window = sentence.split(" "), ""
        for word in words:
            candidate = f"{window} {word}".strip()
            if len(candidate) > limit and window:
                pieces.append(window)
                window = word
            else:
                window = candidate
        if window:
            pieces.append(window)
    return pieces


def _units_from_pages(pages: list[PageText], limit: int) -> list[_Unit]:
    units: list[_Unit] = []
    block = 0
    for page in pages:
        cleaned = normalise_whitespace(page.text)
        if not cleaned:
            continue
        for paragraph in _PARAGRAPH_RE.split(cleaned):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            block += 1
            if len(paragraph) <= limit:
                units.append(_Unit(page.page_number, paragraph, block))
            else:
                units.extend(
                    _Unit(page.page_number, piece, block)
                    for piece in _split_long(paragraph, limit)
                )
    return units


def _join(units: list[_Unit]) -> str:
    """Re-join units, preserving paragraph breaks but not inventing them."""
    parts: list[str] = []
    for index, unit in enumerate(units):
        if index == 0:
            parts.append(unit.text)
        elif unit.block == units[index - 1].block:
            parts.append(" " + unit.text)
        else:
            parts.append("\n\n" + unit.text)
    return "".join(parts).strip()


def _overlap_tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """Trailing units of a chunk, up to ``overlap`` characters, oldest first."""
    if overlap <= 0:
        return []
    tail: list[_Unit] = []
    total = 0
    for unit in reversed(units):
        if total + len(unit.text) > overlap and tail:
            break
        tail.insert(0, unit)
        total += len(unit.text) + 1
    # Never let the overlap swallow the whole chunk: that would loop forever.
    return tail if len(tail) < len(units) else tail[1:]


def chunk_pages(
    pages: list[PageText], chunk_size: int = 1000, chunk_overlap: int = 180
) -> list[TextChunk]:
    """Turn extracted pages into overlapping, page-attributed chunks."""
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    units = _units_from_pages(pages, chunk_size)
    if not units:
        return []

    chunks: list[TextChunk] = []
    buffer: list[_Unit] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        content = _join(buffer)
        if content:
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    page_number=buffer[0].page_number,
                    content=content,
                )
            )
        carry = _overlap_tail(buffer, chunk_overlap)
        buffer = list(carry)
        buffer_len = sum(len(u.text) + 2 for u in buffer)

    for unit in units:
        addition = len(unit.text) + 2
        if buffer and buffer_len + addition > chunk_size:
            flush()
        buffer.append(unit)
        buffer_len += addition

    # Final flush without carrying overlap forward.
    if buffer:
        content = _join(buffer)
        if content:
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    page_number=buffer[0].page_number,
                    content=content,
                )
            )

    return _merge_trailing_scrap(chunks)


def _merge_trailing_scrap(chunks: list[TextChunk]) -> list[TextChunk]:
    """Fold a too-short final chunk back into its predecessor."""
    if len(chunks) > 1 and chunks[-1].char_count < MIN_CHUNK_CHARS:
        scrap = chunks.pop()
        previous = chunks[-1]
        chunks[-1] = TextChunk(
            chunk_index=previous.chunk_index,
            page_number=previous.page_number,
            content=f"{previous.content}\n\n{scrap.content}",
        )
    return chunks
