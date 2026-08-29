"""Unit tests for the chunking strategy."""

from __future__ import annotations

import pytest

from app.services.chunking import (
    MIN_CHUNK_CHARS,
    PageText,
    chunk_pages,
    normalise_whitespace,
)


def test_normalise_collapses_runs_but_keeps_paragraphs():
    raw = "Hello    world\n\n\n\nSecond   paragraph here"
    assert normalise_whitespace(raw) == "Hello world\n\nSecond paragraph here"


def test_normalise_repairs_words_hyphenated_across_line_breaks():
    assert "information" in normalise_whitespace("The infor-\nmation is here.")


def test_chunks_respect_the_size_budget():
    page = PageText(
        1, " ".join(f"Sentence number {i} about revenue." for i in range(200))
    )
    chunks = chunk_pages([page], chunk_size=500, chunk_overlap=80)

    assert len(chunks) > 1
    # Units are whole sentences, so a chunk may overshoot by at most one unit.
    assert all(c.char_count <= 500 + 60 for c in chunks)


def test_consecutive_chunks_overlap():
    page = PageText(1, " ".join(f"Fact {i} is recorded here." for i in range(120)))
    chunks = chunk_pages([page], chunk_size=400, chunk_overlap=120)

    first_tail_words = set(chunks[0].content.split()[-8:])
    second_head_words = set(chunks[1].content.split()[:20])
    assert first_tail_words & second_head_words, "expected shared text across the seam"


def test_chunk_indices_are_dense_and_ordered():
    pages = [PageText(n, f"Page {n} content. " * 60) for n in range(1, 4)]
    chunks = chunk_pages(pages, chunk_size=400, chunk_overlap=50)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_page_attribution_points_at_the_starting_page():
    pages = [PageText(1, "Alpha. " * 100), PageText(2, "Beta. " * 100)]
    chunks = chunk_pages(pages, chunk_size=300, chunk_overlap=0)

    assert chunks[0].page_number == 1
    assert any(c.page_number == 2 for c in chunks)
    assert all(c.page_number in (1, 2) for c in chunks)


def test_empty_pages_produce_no_chunks():
    assert chunk_pages([PageText(1, "   \n\n  ")], 500, 50) == []


def test_short_trailing_fragment_is_folded_into_the_previous_chunk():
    body = "Long body sentence with plenty of words. " * 30
    chunks = chunk_pages([PageText(1, body + "\n\nTiny tail.")], 400, 40)

    assert all(c.char_count >= MIN_CHUNK_CHARS for c in chunks)
    assert "Tiny tail." in chunks[-1].content


def test_very_long_unbroken_sentence_is_split_by_words():
    page = PageText(1, "word " * 400)  # a single 2000-char "sentence"
    chunks = chunk_pages([page], chunk_size=300, chunk_overlap=0)

    assert len(chunks) > 1
    assert all(c.char_count <= 300 + 60 for c in chunks)


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError, match="chunk_overlap"):
        chunk_pages([PageText(1, "text")], chunk_size=100, chunk_overlap=100)
