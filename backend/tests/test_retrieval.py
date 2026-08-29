"""Tests for vector similarity search and its user/document scoping."""

from __future__ import annotations

from app.core.security import hash_password
from app.models import User
from app.services.retrieval import search_chunks
from tests.factories import seed_document

FINANCE_PASSAGES = [
    "Revenue for the 2024 financial year was 4.2 million euros, up 14 percent.",
    "Headcount at the end of 2024 was 58 employees, up from 44 in 2023.",
    "The company was founded in 2019 and is headquartered in Rotterdam.",
]

RECIPE_PASSAGES = [
    "Whisk the eggs with sugar until the mixture is pale and thick.",
    "Bake at 180 degrees for 25 minutes until a skewer comes out clean.",
]


async def _make_user(session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("password123"))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_search_ranks_the_relevant_chunk_first(session, embedding_provider):
    user = await _make_user(session, "owner@example.com")
    await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="annual-report.pdf",
        passages=FINANCE_PASSAGES,
    )

    query = await embedding_provider.embed_query("what was revenue in 2024")
    results = await search_chunks(
        session, user_id=user.id, query_vector=query, top_k=3
    )

    assert results, "expected at least one hit"
    assert "4.2 million euros" in results[0].content
    assert results[0].filename == "annual-report.pdf"
    assert results[0].page_number == 1


async def test_scores_are_sorted_descending(session, embedding_provider):
    user = await _make_user(session, "sorted@example.com")
    await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="report.pdf",
        passages=FINANCE_PASSAGES,
    )

    query = await embedding_provider.embed_query("headcount employees")
    results = await search_chunks(
        session, user_id=user.id, query_vector=query, top_k=3
    )

    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_top_k_limits_the_result_set(session, embedding_provider):
    user = await _make_user(session, "topk@example.com")
    await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="report.pdf",
        passages=FINANCE_PASSAGES,
    )

    query = await embedding_provider.embed_query("company")
    assert len(await search_chunks(session, user_id=user.id, query_vector=query, top_k=1)) == 1
    assert await search_chunks(session, user_id=user.id, query_vector=query, top_k=0) == []


async def test_search_never_crosses_user_boundaries(session, embedding_provider):
    owner = await _make_user(session, "owner2@example.com")
    intruder = await _make_user(session, "intruder@example.com")
    await seed_document(
        session,
        embedding_provider,
        user_id=owner.id,
        filename="private.pdf",
        passages=FINANCE_PASSAGES,
    )

    query = await embedding_provider.embed_query("what was revenue in 2024")
    assert await search_chunks(session, user_id=intruder.id, query_vector=query, top_k=5) == []


async def test_document_filter_restricts_the_search(session, embedding_provider):
    user = await _make_user(session, "filter@example.com")
    finance = await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="report.pdf",
        passages=FINANCE_PASSAGES,
    )
    await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="recipes.pdf",
        passages=RECIPE_PASSAGES,
    )

    query = await embedding_provider.embed_query("bake the eggs and sugar")
    unfiltered = await search_chunks(
        session, user_id=user.id, query_vector=query, top_k=5
    )
    filtered = await search_chunks(
        session,
        user_id=user.id,
        query_vector=query,
        top_k=5,
        document_ids=[finance.id],
    )

    assert any(r.filename == "recipes.pdf" for r in unfiltered)
    assert {r.document_id for r in filtered} == {finance.id}


async def test_min_score_filters_weak_matches(session, embedding_provider):
    user = await _make_user(session, "floor@example.com")
    await seed_document(
        session,
        embedding_provider,
        user_id=user.id,
        filename="recipes.pdf",
        passages=RECIPE_PASSAGES,
    )

    query = await embedding_provider.embed_query(
        "quarterly amortisation of intangible assets"
    )
    assert (
        await search_chunks(
            session, user_id=user.id, query_vector=query, top_k=5, min_score=0.9
        )
        == []
    )


async def test_search_on_an_empty_corpus_returns_nothing(session, embedding_provider):
    user = await _make_user(session, "empty@example.com")
    query = await embedding_provider.embed_query("anything at all")

    assert await search_chunks(session, user_id=user.id, query_vector=query, top_k=5) == []
