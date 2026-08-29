"""Vector similarity search over a user's document chunks.

Two backends, one interface:

* **PostgreSQL + pgvector** - ranking happens in the database with the ``<=>``
  cosine-distance operator, served by the HNSW index created in
  :func:`app.core.database.init_models`. This is the deployment path.
* **SQLite** - candidate rows are loaded and scored with NumPy in-process. This
  exists so the test-suite and a Docker-free local run behave identically; it is
  O(n) in chunks and is not intended for large corpora.

Every query is scoped to ``user_id`` inside the SQL, so one user can never
retrieve another user's text even if an id leaks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document

logger = logging.getLogger(__name__)

#: Loaded into memory per query on the SQLite path. Guards against a runaway
#: corpus turning a dev query into a multi-second scan.
SQLITE_SCAN_LIMIT = 20_000


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk plus the similarity score that got it selected."""

    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_index: int
    content: str
    score: float


def _to_pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


async def _search_postgres(
    session: AsyncSession,
    user_id: str,
    query_vector: list[float],
    top_k: int,
    document_ids: list[str] | None,
) -> list[RetrievedChunk]:
    # `1 - cosine_distance` converts pgvector's distance into a similarity in
    # [-1, 1] so both backends report the same scale to the API.
    sql = """
        SELECT c.id            AS chunk_id,
               c.document_id   AS document_id,
               d.filename      AS filename,
               c.page_number   AS page_number,
               c.chunk_index   AS chunk_index,
               c.content       AS content,
               1 - (c.embedding <=> CAST(:query AS vector)) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.user_id = :user_id
        {document_filter}
        ORDER BY c.embedding <=> CAST(:query AS vector)
        LIMIT :top_k
    """
    params: dict = {
        "query": _to_pgvector_literal(query_vector),
        "user_id": user_id,
        "top_k": top_k,
    }
    document_filter = ""
    if document_ids:
        document_filter = "AND c.document_id = ANY(:document_ids)"
        params["document_ids"] = list(document_ids)

    result = await session.execute(
        text(sql.format(document_filter=document_filter)), params
    )
    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            filename=row.filename,
            page_number=row.page_number,
            chunk_index=row.chunk_index,
            content=row.content,
            score=float(row.score),
        )
        for row in result
    ]


async def _search_in_memory(
    session: AsyncSession,
    user_id: str,
    query_vector: list[float],
    top_k: int,
    document_ids: list[str] | None,
) -> list[RetrievedChunk]:
    stmt = (
        select(Chunk, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.user_id == user_id)
        .limit(SQLITE_SCAN_LIMIT)
    )
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))

    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    matrix = np.asarray([row[0].embedding for row in rows], dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32)

    # Vectors are stored L2-normalised, but renormalise defensively so a vector
    # written by an older provider config cannot skew the ranking.
    matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix_norms[matrix_norms == 0] = 1.0
    query_norm = float(np.linalg.norm(query)) or 1.0
    scores = (matrix / matrix_norms) @ (query / query_norm)

    top_indices = np.argsort(-scores)[:top_k]
    return [
        RetrievedChunk(
            chunk_id=rows[i][0].id,
            document_id=rows[i][0].document_id,
            filename=rows[i][1],
            page_number=rows[i][0].page_number,
            chunk_index=rows[i][0].chunk_index,
            content=rows[i][0].content,
            score=float(scores[i]),
        )
        for i in top_indices
    ]


async def search_chunks(
    session: AsyncSession,
    *,
    user_id: str,
    query_vector: list[float],
    top_k: int = 5,
    document_ids: list[str] | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Return the ``top_k`` chunks most similar to ``query_vector``.

    Args:
        user_id: Owner scope; results never cross user boundaries.
        document_ids: Optional allow-list to search a subset of documents.
        min_score: Optional cosine-similarity floor. Left unset by the chat
            endpoint on purpose - the model is prompted to say when the context
            does not answer the question, which is more reliable than guessing a
            universal threshold that would differ per embedding provider.
    """
    if top_k <= 0:
        return []

    backend = session.get_bind().dialect.name
    if backend == "postgresql":
        results = await _search_postgres(
            session, user_id, query_vector, top_k, document_ids
        )
    else:
        results = await _search_in_memory(
            session, user_id, query_vector, top_k, document_ids
        )

    if min_score is not None:
        results = [r for r in results if r.score >= min_score]
    return results
