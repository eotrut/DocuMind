"""Document ingestion pipeline: PDF bytes in, embedded chunks out.

    upload -> validate -> extract pages -> chunk -> embed -> persist

Ingestion runs inline with the upload request so the HTTP response already
carries the document's final state (``ready`` or ``failed``) and the UI never
has to poll. The trade-off - a large PDF holds the request open while Gemini
embeds it - is called out in the README's "what I'd improve" section.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import DocuMindError
from app.models import Chunk, Document
from app.services.chunking import chunk_pages
from app.services.embeddings import EmbeddingProvider
from app.services.pdf import PdfExtractionError, extract_pages

logger = logging.getLogger(__name__)


class IngestionError(DocuMindError):
    """A single file could not be ingested; other files in the batch continue."""

    status_code = 400


def validate_upload(filename: str, data: bytes) -> None:
    """Reject obviously-bad uploads before spending any CPU or quota on them."""
    if not filename.lower().endswith(".pdf"):
        raise IngestionError("Only PDF files are supported.")
    if not data:
        raise IngestionError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise IngestionError(
            f"File is larger than the {settings.max_upload_mb} MB limit."
        )


async def ingest_pdf(
    session: AsyncSession,
    *,
    user_id: str,
    filename: str,
    data: bytes,
    provider: EmbeddingProvider,
) -> Document:
    """Ingest one PDF and return the persisted :class:`Document`.

    On failure the document row is still written with ``status='failed'`` and a
    human-readable ``error``, so the UI can show what went wrong instead of the
    file silently vanishing.
    """
    validate_upload(filename, data)

    document = Document(
        user_id=user_id,
        filename=filename,
        size_bytes=len(data),
        status="processing",
    )
    session.add(document)
    await session.flush()  # assigns document.id without committing

    try:
        pages = extract_pages(data)
    except PdfExtractionError as exc:
        document.status = "failed"
        document.error = str(exc)
        await session.commit()
        raise IngestionError(str(exc)) from exc

    chunks = chunk_pages(
        pages, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )
    if not chunks:
        document.status = "failed"
        document.error = "No text could be extracted from this PDF."
        await session.commit()
        raise IngestionError(document.error)

    try:
        vectors = await provider.embed_documents([c.content for c in chunks])
    except Exception as exc:
        document.status = "failed"
        document.error = "Failed to generate embeddings for this document."
        await session.commit()
        logger.exception("Embedding failed for %s", filename)
        # Rate-limit / upstream errors carry their own status codes; re-raise so
        # the caller can report 429 rather than a generic 400.
        if isinstance(exc, DocuMindError):
            raise
        raise IngestionError(document.error) from exc

    session.add_all(
        Chunk(
            document_id=document.id,
            user_id=user_id,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            content=chunk.content,
            char_count=chunk.char_count,
            embedding=vector,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    )

    document.page_count = len(pages)
    document.chunk_count = len(chunks)
    document.status = "ready"
    document.error = None
    await session.commit()
    await session.refresh(document)

    logger.info(
        "Ingested %s: %d pages, %d chunks for user %s",
        filename,
        document.page_count,
        document.chunk_count,
        user_id,
    )
    return document
