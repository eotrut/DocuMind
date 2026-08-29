"""Helpers for building test fixtures (synthetic PDFs, seeded chunks)."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from app.models import Chunk, Document

SAMPLE_PAGES: list[list[str]] = [
    [
        "Northwind Analytics - Annual Report 2024",
        "",
        "Northwind Analytics is a data tooling company founded in 2019 and",
        "headquartered in Rotterdam. The company sells a managed warehouse",
        "product to mid-market retailers across the European Union.",
    ],
    [
        "Financial performance",
        "",
        "Revenue for the 2024 financial year was 4.2 million euros, an increase",
        "of 14 percent compared with the 3.7 million euros recorded in 2023.",
        "Gross margin improved to 71 percent from 66 percent a year earlier.",
        "Operating expenses were 3.1 million euros for the year.",
    ],
    [
        "People and operations",
        "",
        "Headcount at the end of the 2024 financial year was 58 employees,",
        "up from 44 at the end of 2023. The engineering team accounts for",
        "31 of those employees. Voluntary attrition for the year was 6 percent.",
    ],
]


def build_sample_pdf(pages: list[list[str]] | None = None) -> bytes:
    """Render a small, deterministic, text-bearing PDF in memory."""
    pages = pages or SAMPLE_PAGES
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    for lines in pages:
        text_object = pdf.beginText(72, 720)
        text_object.setFont("Helvetica", 12)
        for line in lines:
            text_object.textLine(line)
        pdf.drawText(text_object)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_blank_pdf(page_count: int = 1) -> bytes:
    """A valid PDF with no extractable text (stands in for a scan)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    for _ in range(page_count):
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


async def seed_document(
    session, provider, *, user_id: str, filename: str, passages: list[str]
) -> Document:
    """Insert a document plus one chunk per passage, with real embeddings."""
    document = Document(
        user_id=user_id,
        filename=filename,
        size_bytes=1024,
        page_count=len(passages),
        chunk_count=len(passages),
        status="ready",
    )
    session.add(document)
    await session.flush()

    vectors = await provider.embed_documents(passages)
    session.add_all(
        Chunk(
            document_id=document.id,
            user_id=user_id,
            chunk_index=index,
            page_number=index + 1,
            content=passage,
            char_count=len(passage),
            embedding=vector,
        )
        for index, (passage, vector) in enumerate(zip(passages, vectors, strict=True))
    )
    await session.commit()
    await session.refresh(document)
    return document
