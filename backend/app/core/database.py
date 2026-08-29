"""Async SQLAlchemy engine/session wiring.

Two backends are supported on purpose:

* **PostgreSQL + pgvector** — the real deployment target (docker-compose).
* **SQLite** — a zero-infrastructure fallback so the test-suite and a quick
  local run work without Docker. Vector search then happens in NumPy instead of
  in the database; see ``app.services.retrieval``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def create_engine(url: str | None = None) -> AsyncEngine:
    url = url or settings.database_url
    kwargs: dict = {"echo": False, "future": True}
    if url.startswith("postgresql"):
        # Modest pool: the API is I/O bound and mostly waits on Gemini.
        kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True)
    return create_async_engine(url, **kwargs)


engine: AsyncEngine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_models(target_engine: AsyncEngine | None = None) -> None:
    """Create the pgvector extension (Postgres only) and all tables.

    Deliberately schema-create rather than Alembic: the schema is small, and a
    migration tool would be ceremony for a single-service portfolio project.
    The trade-off is noted in the README.
    """
    from sqlalchemy import text

    from app import models  # noqa: F401  (import registers the mappers)

    eng = target_engine or engine
    async with eng.begin() as conn:
        is_pg = eng.url.get_backend_name() == "postgresql"
        if is_pg:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        if is_pg:
            # HNSW beats IVFFlat for a corpus this size: no training step, good
            # recall out of the box, and it stays correct as rows are added.
            # Created in raw SQL so the SQLite fallback never sees it.
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
                    "ON chunks USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
