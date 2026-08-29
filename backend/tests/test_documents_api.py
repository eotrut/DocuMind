"""End-to-end tests for document upload, listing and deletion."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Chunk
from tests.factories import build_blank_pdf, build_sample_pdf


def _pdf_upload(name: str = "annual-report.pdf", data: bytes | None = None):
    return {"files": (name, data if data is not None else build_sample_pdf(), "application/pdf")}


async def test_upload_indexes_a_pdf_into_chunks(auth_client, session):
    response = await auth_client.post("/api/documents", files=_pdf_upload())

    assert response.status_code == 201, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True
    document = result["document"]
    assert document["status"] == "ready"
    assert document["page_count"] == 3
    assert document["chunk_count"] >= 1
    assert document["error"] is None

    stored = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document["id"])
    )
    assert stored == document["chunk_count"]


async def test_uploaded_document_appears_in_the_list(auth_client):
    await auth_client.post("/api/documents", files=_pdf_upload())
    response = await auth_client.get("/api/documents")

    assert response.status_code == 200
    assert [d["filename"] for d in response.json()] == ["annual-report.pdf"]


async def test_a_non_pdf_is_reported_as_a_failed_result_not_a_crash(auth_client):
    response = await auth_client.post(
        "/api/documents", files={"files": ("notes.txt", b"hello", "text/plain")}
    )

    assert response.status_code == 201
    result = response.json()["results"][0]
    assert result["ok"] is False
    assert "Only PDF" in result["error"]
    assert (await auth_client.get("/api/documents")).json() == []


async def test_a_pdf_without_a_text_layer_fails_with_guidance(auth_client):
    response = await auth_client.post(
        "/api/documents", files=_pdf_upload("scan.pdf", build_blank_pdf(1))
    )

    result = response.json()["results"][0]
    assert result["ok"] is False
    assert "OCR" in result["error"]


async def test_a_bad_file_does_not_prevent_a_good_one_from_indexing(auth_client):
    response = await auth_client.post(
        "/api/documents",
        files=[
            ("files", ("broken.pdf", b"%PDF-1.4 truncated", "application/pdf")),
            ("files", ("annual-report.pdf", build_sample_pdf(), "application/pdf")),
        ],
    )

    results = {r["filename"]: r["ok"] for r in response.json()["results"]}
    assert results == {"broken.pdf": False, "annual-report.pdf": True}


async def test_chunks_endpoint_returns_the_source_text(auth_client):
    upload = await auth_client.post("/api/documents", files=_pdf_upload())
    document_id = upload.json()["results"][0]["document"]["id"]

    response = await auth_client.get(f"/api/documents/{document_id}/chunks")

    assert response.status_code == 200
    chunks = response.json()
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert any("Northwind" in c["content"] for c in chunks)


async def test_deleting_a_document_also_deletes_its_vectors(auth_client, session):
    upload = await auth_client.post("/api/documents", files=_pdf_upload())
    document_id = upload.json()["results"][0]["document"]["id"]

    response = await auth_client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    assert (await auth_client.get("/api/documents")).json() == []
    remaining = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
    )
    assert remaining == 0


async def test_another_users_document_is_not_reachable(auth_client, client):
    upload = await auth_client.post("/api/documents", files=_pdf_upload())
    document_id = upload.json()["results"][0]["document"]["id"]

    signup = await client.post(
        "/api/auth/signup",
        json={"email": "intruder@example.com", "password": "another-password-1"},
    )
    client.headers["Authorization"] = f"Bearer {signup.json()['access_token']}"

    assert (await client.get(f"/api/documents/{document_id}")).status_code == 404
    assert (await client.delete(f"/api/documents/{document_id}")).status_code == 404
    assert (await client.get(f"/api/documents/{document_id}/chunks")).status_code == 404


async def test_upload_requires_authentication(client):
    response = await client.post("/api/documents", files=_pdf_upload())
    assert response.status_code == 401
