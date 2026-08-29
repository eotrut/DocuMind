"""Unit tests for prompt assembly and citation handling."""

from __future__ import annotations

from app.services import rag
from app.services.retrieval import RetrievedChunk


def _chunk(chunk_id: str, content: str, page: int = 1, score: float = 0.9):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        filename="report.pdf",
        page_number=page,
        chunk_index=0,
        content=content,
        score=score,
    )


def test_citations_are_numbered_from_one_in_ranked_order():
    citations = rag.build_citations(
        [_chunk("a", "First", score=0.9), _chunk("b", "Second", score=0.4)]
    )

    assert [c.marker for c in citations] == [1, 2]
    assert [c.chunk_id for c in citations] == ["a", "b"]
    assert citations[0].score == 0.9


def test_long_chunks_are_snipped_on_a_word_boundary():
    citation = rag.build_citations([_chunk("a", "word " * 400)])[0]

    assert len(citation.snippet) <= rag.SNIPPET_CHARS + 1
    assert citation.snippet.endswith("…")
    assert not citation.snippet.rstrip("…").endswith("wor")


def test_short_chunks_are_not_snipped():
    citation = rag.build_citations([_chunk("a", "Short passage.")])[0]
    assert citation.snippet == "Short passage."


def test_prompt_contains_every_source_with_its_marker_and_page():
    citations = rag.build_citations(
        [_chunk("a", "Revenue was 4.2 million euros.", page=3)]
    )
    prompt = rag.build_prompt("What was revenue?", citations)

    assert "[1] report.pdf (page 3)" in prompt
    assert "Revenue was 4.2 million euros." in prompt
    assert "What was revenue?" in prompt
    assert prompt.index("SOURCES") < prompt.index("QUESTION")


def test_used_markers_are_deduplicated_and_kept_in_first_use_order():
    citations = rag.build_citations(
        [_chunk("a", "one"), _chunk("b", "two"), _chunk("c", "three")]
    )
    answer = "Growth was strong [2]. Margins improved [1][2]."

    assert rag.used_markers(answer, citations) == [2, 1]


def test_hallucinated_markers_are_discarded():
    citations = rag.build_citations([_chunk("a", "one")])
    assert rag.used_markers("As shown in [7] and [1].", citations) == [1]


def test_an_uncited_answer_yields_no_markers():
    citations = rag.build_citations([_chunk("a", "one")])
    assert rag.used_markers("I could not find that in your documents.", citations) == []


def test_history_is_capped_and_roles_are_mapped_to_gemini_names():
    history = [("user", f"question {i}") for i in range(20)]
    contents = rag._history_contents(history)

    assert len(contents) == rag.HISTORY_TURNS
    assert contents[0].role == "user"
    assert rag._history_contents([("assistant", "hi")])[0].role == "model"


def test_blank_history_turns_are_dropped():
    assert rag._history_contents([("user", "   ")]) == []


def test_stale_citation_markers_are_stripped_from_replayed_assistant_turns():
    contents = rag._history_contents(
        [("assistant", "Revenue grew 14% [2] and margins improved [1].")]
    )

    text = contents[0].parts[0].text
    assert "[2]" not in text and "[1]" not in text
    assert "Revenue grew 14%" in text


def test_markers_in_the_users_own_question_are_left_alone():
    contents = rag._history_contents([("user", "What did source [2] say?")])
    assert contents[0].parts[0].text == "What did source [2] say?"


def test_an_assistant_turn_that_is_only_markers_is_dropped():
    assert rag._history_contents([("assistant", "[1][2]")]) == []
