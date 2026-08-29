"""End-to-end smoke test against a running DocuMind API.

Exercises the full path a real user takes: sign up, upload a PDF, ask a
question, and check that the streamed answer comes back with resolvable
citations. Unlike the pytest suite, this calls Gemini for real, so it needs a
valid ``GEMINI_API_KEY``.

Usage::

    python scripts/smoke_test.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import uuid

import httpx
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

PAGES: list[list[str]] = [
    [
        "Meridian Robotics - Annual Report 2031",
        "",
        "Meridian Robotics designs warehouse automation systems and was",
        "founded in 2024 in Porto, Portugal.",
    ],
    [
        "Financial performance",
        "",
        "Revenue for the 2031 financial year was 18.4 million euros, an increase",
        "of 27 percent on the 14.5 million euros recorded in 2030.",
        "Gross margin was 63 percent.",
    ],
    [
        "People",
        "",
        "Headcount at the end of 2031 was 212 employees, of whom 96 work in",
        "engineering. Voluntary attrition was 4 percent for the year.",
    ],
]

QUESTION = "What was revenue in the 2031 financial year, and how much did it grow?"


def build_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    for lines in PAGES:
        text = pdf.beginText(72, 720)
        text.setFont("Helvetica", 12)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def step(label: str) -> None:
    print(f"\n\033[1m>> {label}\033[0m")


def ok(message: str) -> None:
    print(f"   [PASS] {message}")


def fail(message: str) -> None:
    print(f"   [FAIL] {message}")
    raise SystemExit(1)


async def main(base_url: str) -> None:
    email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
    password = "smoke-test-password-1"

    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        step("Health check")
        health = await client.get("/health")
        if health.status_code != 200:
            fail(f"/health returned {health.status_code}")
        body = health.json()
        ok(f"API up (model={body['model']}, embeddings={body['embedding_provider']})")
        if not body["gemini_key_configured"]:
            fail("GEMINI_API_KEY is not configured on the server")

        step("Sign up")
        signup = await client.post(
            "/api/auth/signup", json={"email": email, "password": password}
        )
        if signup.status_code != 201:
            fail(f"signup failed: {signup.status_code} {signup.text}")
        token = signup.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        ok(f"registered {email}")

        step("Upload a synthetic PDF")
        upload = await client.post(
            "/api/documents",
            headers=auth,
            files={"files": ("meridian-2031.pdf", build_pdf(), "application/pdf")},
        )
        if upload.status_code != 201:
            fail(f"upload failed: {upload.status_code} {upload.text}")
        result = upload.json()["results"][0]
        if not result["ok"]:
            fail(f"ingestion failed: {result['error']}")
        document = result["document"]
        ok(
            f"indexed {document['filename']}: {document['page_count']} pages, "
            f"{document['chunk_count']} chunks"
        )

        step("Ask a question (streaming)")
        citations: list[dict] = []
        answer_parts: list[str] = []
        used_markers: list[int] = []
        error_detail: str | None = None

        async with client.stream(
            "POST", "/api/chat/stream", headers=auth, json={"message": QUESTION}
        ) as response:
            if response.status_code != 200:
                await response.aread()
                fail(f"chat failed: {response.status_code} {response.text}")

            event = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:].strip()
                elif line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if event == "sources":
                        citations = payload["citations"]
                    elif event == "token":
                        answer_parts.append(payload["text"])
                    elif event == "done":
                        used_markers = payload["used_markers"]
                    elif event == "error":
                        error_detail = payload["detail"]

        if error_detail:
            fail(f"the model stream reported: {error_detail}")

        answer = "".join(answer_parts).strip()
        if not answer:
            fail("the answer was empty")
        print(f"\n   Q: {QUESTION}")
        print(f"   A: {answer}\n")

        if not citations:
            fail("no sources were retrieved")
        ok(f"retrieved {len(citations)} sources")

        if not used_markers:
            fail("the answer contained no citation markers")
        ok(f"answer cited markers {used_markers}")

        cited = [c for c in citations if c["marker"] in used_markers]
        for citation in cited:
            ok(
                f"[{citation['marker']}] -> {citation['filename']} "
                f"p.{citation['page_number']} "
                f"(score {citation['score']:.3f})"
            )

        if "18.4" not in answer:
            fail("the answer did not contain the expected revenue figure (18.4)")
        ok("answer contains the grounded figure from the PDF")

        step("Conversation history")
        conversations = (await client.get("/api/conversations", headers=auth)).json()
        if not conversations:
            fail("the conversation was not persisted")
        detail = (
            await client.get(f"/api/conversations/{conversations[0]['id']}", headers=auth)
        ).json()
        roles = [m["role"] for m in detail["messages"]]
        if roles != ["user", "assistant"]:
            fail(f"unexpected message roles: {roles}")
        if not detail["messages"][1]["citations"]:
            fail("citations were not persisted with the assistant message")
        ok("conversation persisted with its citations")

        step("Delete the document")
        deleted = await client.delete(f"/api/documents/{document['id']}", headers=auth)
        if deleted.status_code != 204:
            fail(f"delete failed: {deleted.status_code}")
        if (await client.get("/api/documents", headers=auth)).json():
            fail("the document is still listed after deletion")
        ok("document and its vectors removed")

    print("\n\033[1;32mAll smoke checks passed.\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.base_url))
    except KeyboardInterrupt:
        sys.exit(130)
