"""Static checks on the PostgreSQL/pgvector code path.

The test-suite runs on SQLite, so the pgvector branch is never *executed* here.
These tests compile it against SQLAlchemy's PostgreSQL dialect instead, which
catches the mistakes that path is actually prone to: a vector column that does
not render as ``VECTOR(n)``, a bind parameter the driver cannot type, or a
missing cast on the distance operator.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from app.core.config import settings
from app.models import Chunk
from app.services.retrieval import build_postgres_statement

PG = postgresql.dialect()
QUERY_VECTOR = [0.1, -0.2, 0.3]


def _render(document_ids: list[str] | None) -> str:
    statement, _ = build_postgres_statement("user-1", QUERY_VECTOR, 5, document_ids)
    return str(statement.compile(dialect=PG, compile_kwargs={"literal_binds": False}))


def test_embedding_column_renders_as_a_pgvector_column():
    column_type = Chunk.__table__.c.embedding.type
    assert column_type.compile(PG).upper() == f"VECTOR({settings.embedding_dim})"


def test_embedding_column_falls_back_to_text_on_sqlite():
    assert "TEXT" in Chunk.__table__.c.embedding.type.compile(sqlite.dialect()).upper()


def test_create_table_ddl_is_valid_for_postgres():
    ddl = str(CreateTable(Chunk.__table__).compile(dialect=PG))
    assert f"VECTOR({settings.embedding_dim})" in ddl.upper()
    assert "FOREIGN KEY" in ddl.upper()


def test_query_casts_the_bound_vector_and_orders_by_distance():
    sql = _render(None)
    assert "CAST(%(query)s AS vector)" in sql
    assert "<=>" in sql
    assert "ORDER BY" in sql
    assert "LIMIT" in sql


def test_query_always_scopes_to_the_owning_user():
    assert "c.user_id = %(user_id)s" in _render(None)


def test_document_filter_expands_to_an_in_clause():
    # `= ANY(:ids)` would leave asyncpg unable to infer the array element type
    # against a varchar column; an expanding IN sidesteps that entirely.
    statement, params = build_postgres_statement(
        "user-1", QUERY_VECTOR, 5, ["doc-a", "doc-b"]
    )
    # Binding the values lets the compiler expand the IN list, which is where a
    # mistyped or non-expanding parameter would show up.
    sql = str(
        statement.bindparams(**params).compile(
            dialect=PG, compile_kwargs={"render_postcompile": True}
        )
    )
    assert "c.document_id IN (%(document_ids_1)s, %(document_ids_2)s)" in sql
    assert "ANY(" not in sql
    assert params["document_ids"] == ["doc-a", "doc-b"]


def test_no_document_filter_is_rendered_when_none_is_requested():
    sql = _render(None)
    assert "document_id IN" not in sql


def test_the_bound_vector_is_a_pgvector_literal():
    _, params = build_postgres_statement("user-1", QUERY_VECTOR, 5, None)
    assert params["query"].startswith("[")
    assert params["query"].endswith("]")
    assert params["query"].count(",") == len(QUERY_VECTOR) - 1
    assert params["top_k"] == 5
