"""Upload, list, inspect and delete a user's documents."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select

from app.core.errors import DocuMindError
from app.dependencies import CurrentUser, ProviderDep, SessionDep
from app.models import Chunk, Document
from app.schemas import ChunkRead, DocumentRead, UploadResponse, UploadResult
from app.services.ingestion import ingest_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILES_PER_REQUEST = 10


@router.get("", response_model=list[DocumentRead])
async def list_documents(user: CurrentUser, session: SessionDep) -> list[DocumentRead]:
    rows = (
        await session.execute(
            select(Document)
            .where(Document.user_id == user.id)
            .order_by(Document.created_at.desc())
        )
    ).scalars()
    return [DocumentRead.model_validate(doc) for doc in rows]


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_documents(
    user: CurrentUser,
    session: SessionDep,
    provider: ProviderDep,
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """Ingest one or more PDFs.

    Each file is handled independently: one bad PDF in a batch is reported as a
    failed :class:`UploadResult` while the rest still index successfully.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Please upload at most {MAX_FILES_PER_REQUEST} files at a time.",
        )

    results: list[UploadResult] = []
    for upload in files:
        filename = upload.filename or "document.pdf"
        try:
            data = await upload.read()
            document = await ingest_pdf(
                session,
                user_id=user.id,
                filename=filename,
                data=data,
                provider=provider,
            )
            results.append(
                UploadResult(
                    filename=filename,
                    ok=True,
                    document=DocumentRead.model_validate(document),
                )
            )
        except DocuMindError as exc:
            await session.rollback()
            logger.info("Upload rejected for %s: %s", filename, exc.message)
            results.append(
                UploadResult(filename=filename, ok=False, error=exc.message)
            )
        except Exception:  # noqa: BLE001 - never fail the whole batch on one file
            await session.rollback()
            logger.exception("Unexpected error ingesting %s", filename)
            results.append(
                UploadResult(
                    filename=filename,
                    ok=False,
                    error="An unexpected error occurred while processing this file.",
                )
            )
        finally:
            await upload.close()

    return UploadResponse(results=results)


async def _get_owned_document(session, user_id: str, document_id: str) -> Document:
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id, Document.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if document is None:
        # 404 rather than 403: don't confirm the existence of other users' ids.
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str, user: CurrentUser, session: SessionDep
) -> DocumentRead:
    document = await _get_owned_document(session, user.id, document_id)
    return DocumentRead.model_validate(document)


@router.get("/{document_id}/chunks", response_model=list[ChunkRead])
async def list_document_chunks(
    document_id: str, user: CurrentUser, session: SessionDep
) -> list[ChunkRead]:
    """Full chunk text for a document - backs the citation side panel."""
    await _get_owned_document(session, user.id, document_id)
    rows = (
        await session.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.user_id == user.id)
            .order_by(Chunk.chunk_index)
        )
    ).scalars()
    return [ChunkRead.model_validate(chunk) for chunk in rows]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, user: CurrentUser, session: SessionDep
) -> None:
    """Delete a document and every vector derived from it."""
    document = await _get_owned_document(session, user.id, document_id)
    # Explicit chunk delete: SQLite does not enforce ON DELETE CASCADE unless
    # PRAGMA foreign_keys is on, so we do not rely on it.
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.delete(document)
    await session.commit()
