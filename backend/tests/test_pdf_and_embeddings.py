"""Unit tests for PDF extraction and the embedding providers."""

from __future__ import annotations

import math

import pytest

from app.services.embeddings import FakeEmbeddingProvider, l2_normalise
from app.services.pdf import PdfExtractionError, extract_pages, looks_like_pdf
from tests.factories import build_blank_pdf, build_sample_pdf

# ------------------------------------------------------------------ PDF parsing


def test_extract_pages_returns_one_entry_per_page_with_text():
    pages = extract_pages(build_sample_pdf())

    assert [p.page_number for p in pages] == [1, 2, 3]
    assert "Northwind Analytics" in pages[0].text
    assert "4.2 million euros" in pages[1].text
    assert "58 employees" in pages[2].text


def test_non_pdf_upload_is_rejected():
    with pytest.raises(PdfExtractionError, match="not a valid PDF"):
        extract_pages(b"just some text pretending to be a document")


def test_empty_upload_is_rejected():
    with pytest.raises(PdfExtractionError, match="empty"):
        extract_pages(b"")


def test_pdf_without_a_text_layer_is_rejected_with_ocr_guidance():
    with pytest.raises(PdfExtractionError, match="OCR"):
        extract_pages(build_blank_pdf(2))


def test_magic_number_check():
    assert looks_like_pdf(build_sample_pdf())
    assert not looks_like_pdf(b"PK\x03\x04")


# -------------------------------------------------------------------- embeddings


async def test_fake_provider_is_deterministic_and_normalised():
    provider = FakeEmbeddingProvider(256)
    first = await provider.embed_query("quarterly revenue growth")
    second = await provider.embed_query("quarterly revenue growth")

    assert first == second
    assert len(first) == 256
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-6)


async def test_fake_provider_ranks_lexically_related_text_higher():
    provider = FakeEmbeddingProvider(256)
    documents = [
        "The cat sat quietly on the woven mat by the door.",
        "Revenue for the financial year was 4.2 million euros, up 14 percent.",
    ]
    vectors = await provider.embed_documents(documents)
    query = await provider.embed_query("how much revenue did the company make")

    scores = [
        sum(q * d for q, d in zip(query, vector, strict=True)) for vector in vectors
    ]
    assert scores[1] > scores[0]


async def test_embed_documents_preserves_input_order_and_count():
    provider = FakeEmbeddingProvider(256)
    texts = ["alpha", "beta", "gamma"]
    vectors = await provider.embed_documents(texts)

    assert len(vectors) == 3
    assert vectors[1] == await provider.embed_query("beta")


def test_l2_normalise_handles_the_zero_vector():
    assert l2_normalise([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
