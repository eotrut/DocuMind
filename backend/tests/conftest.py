"""Shared pytest fixtures.

The environment is configured *before* any ``app`` module is imported, because
settings (and therefore the embedding column width) are resolved at import time.
Tests run against a throwaway SQLite file with the deterministic ``fake``
embedding provider, so the suite needs neither Docker nor a Gemini API key.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

TEST_DB_PATH = Path(tempfile.gettempdir()) / "documind_test.sqlite3"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["EMBEDDING_DIM"] = "256"
os.environ["GEMINI_API_KEY"] = "test-key-not-used"
os.environ["GEMINI_MODEL"] = "gemini-test"
os.environ["JWT_SECRET"] = "test-secret-long-enough-for-hs256-signing"
os.environ["CHUNK_SIZE"] = "600"
os.environ["CHUNK_OVERLAP"] = "100"
os.environ["RETRIEVAL_TOP_K"] = "3"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.core.database import SessionLocal, engine, init_models  # noqa: E402
from app.dependencies import get_provider  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Chunk, Conversation, Document, Message, User  # noqa: E402
from app.services.embeddings import build_provider  # noqa: E402


def _remove_test_db() -> None:
    # Windows keeps a lock on the file while the connection pool is still open,
    # so removal is best-effort. Row-level cleanup below is what guarantees
    # isolation; deleting the file is only housekeeping.
    with suppress(OSError):
        TEST_DB_PATH.unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    """Start every session from an empty database file."""
    _remove_test_db()
    yield
    _remove_test_db()


@pytest.fixture(autouse=True)
async def _prepared_schema():
    """Create the schema once, then wipe rows between tests for isolation."""
    await init_models(engine)
    async with SessionLocal() as session:
        for model in (Message, Conversation, Chunk, Document, User):
            await session.execute(delete(model))
        await session.commit()
    yield


@pytest.fixture
def embedding_provider():
    """The deterministic provider the tests assert against."""
    return build_provider("fake", 256)


@pytest.fixture
async def session():
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def client(embedding_provider):
    """HTTP client bound directly to the ASGI app (no network, no server)."""
    app.dependency_overrides[get_provider] = lambda: embedding_provider
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_client(client):
    """A client with a registered user's bearer token already attached."""
    response = await client.post(
        "/api/auth/signup",
        json={"email": "ada@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    client.user_id = body["user"]["id"]  # type: ignore[attr-defined]
    return client
