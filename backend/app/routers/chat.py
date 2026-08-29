"""Chat endpoints: RAG answers over SSE, plus conversation history.

Streaming protocol (``text/event-stream``), one JSON object per event:

===========  ==========================================================
``meta``     ``{conversation_id, user_message_id}`` - sent first so the UI
             can attach the exchange to a conversation immediately.
``sources``  ``{citations: [...]}`` - the retrieved chunks with their ``[n]``
             markers, sent *before* the first token so a marker can be
             rendered as a live popover the moment it streams in.
``token``    ``{text}`` - an answer fragment.
``done``     ``{message_id, used_markers}`` - the markers the model actually
             cited, so the UI lists only sources that were used.
``error``    ``{detail, code}`` - a terminal, user-presentable failure.
===========  ==========================================================

Errors are delivered as ``error`` events rather than HTTP status codes because
the response headers are already on the wire once streaming has started.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import DocuMindError
from app.dependencies import CurrentUser, ProviderDep, SessionDep
from app.models import Conversation, Message
from app.schemas import (
    ChatRequest,
    Citation,
    ConversationDetail,
    ConversationRead,
    MessageRead,
)
from app.services import rag
from app.services.retrieval import search_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

#: Previous turns replayed to the model for follow-up questions.
HISTORY_MESSAGE_LIMIT = 8
TITLE_MAX_CHARS = 60


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _title_from(message: str) -> str:
    title = " ".join(message.split())
    return title[: TITLE_MAX_CHARS - 1] + "…" if len(title) > TITLE_MAX_CHARS else title


async def _load_conversation(
    session: AsyncSession, user_id: str, conversation_id: str
) -> Conversation:
    conversation = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


# --------------------------------------------------------------------------- chat
@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    user: CurrentUser,
    session: SessionDep,
    provider: ProviderDep,
) -> StreamingResponse:
    """Answer a question from the user's documents, streaming the response."""
    user_id = user.id

    # ---- 1. Conversation bookkeeping -------------------------------------
    if payload.conversation_id:
        conversation = await _load_conversation(
            session, user_id, payload.conversation_id
        )
    else:
        conversation = Conversation(user_id=user_id, title=_title_from(payload.message))
        session.add(conversation)
        await session.flush()

    history = [
        (message.role, message.content)
        for message in (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc())
                .limit(HISTORY_MESSAGE_LIMIT)
            )
        ).scalars()
    ][::-1]

    user_message = Message(
        conversation_id=conversation.id, role="user", content=payload.message
    )
    session.add(user_message)
    await session.commit()

    conversation_id = conversation.id
    user_message_id = user_message.id

    # ---- 2. Retrieval ----------------------------------------------------
    try:
        query_vector = await provider.embed_query(payload.message)
        retrieved = await search_chunks(
            session,
            user_id=user_id,
            query_vector=query_vector,
            top_k=settings.retrieval_top_k,
            document_ids=payload.document_ids or None,
        )
    except DocuMindError as exc:
        # Retrieval failed before any bytes were streamed, so a real HTTP status
        # is still possible - and far easier for the client to handle.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    citations = rag.build_citations(retrieved)

    async def event_stream() -> AsyncIterator[str]:
        yield _sse(
            "meta",
            {"conversation_id": conversation_id, "user_message_id": user_message_id},
        )
        yield _sse(
            "sources",
            {"citations": [c.model_dump() for c in citations]},
        )

        collected: list[str] = []
        try:
            if not citations:
                # Nothing retrieved: answer deterministically instead of burning
                # a Gemini call on an empty context.
                collected.append(rag.NO_CONTEXT_MESSAGE)
                yield _sse("token", {"text": rag.NO_CONTEXT_MESSAGE})
            else:
                async for piece in rag.stream_answer(
                    payload.message, citations, history
                ):
                    collected.append(piece)
                    yield _sse("token", {"text": piece})
        except DocuMindError as exc:
            logger.info("Chat stream ended with a handled error: %s", exc.message)
            yield _sse("error", {"detail": exc.message, "code": exc.status_code})
            return
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            logger.exception("Unexpected error while streaming an answer")
            yield _sse(
                "error",
                {"detail": "Something went wrong generating the answer.", "code": 500},
            )
            return

        answer = "".join(collected).strip()
        markers = rag.used_markers(answer, citations)
        cited = [c for c in citations if c.marker in markers] if markers else []

        # A fresh session: the request-scoped one may already be torn down by the
        # time the generator finishes streaming.
        async with SessionLocal() as write_session:
            assistant_message = Message(
                conversation_id=conversation_id, role="assistant", content=answer
            )
            assistant_message.citations = [c.model_dump() for c in cited]
            write_session.add(assistant_message)
            conversation_row = await write_session.get(Conversation, conversation_id)
            if conversation_row is not None:
                # Touch the conversation so the sidebar sorts by recent activity.
                conversation_row.updated_at = datetime.now(timezone.utc)
            await write_session.commit()
            message_id = assistant_message.id

        yield _sse("done", {"message_id": message_id, "used_markers": markers})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx et al.)
            "Connection": "keep-alive",
        },
    )


# -------------------------------------------------------------------- history
@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    user: CurrentUser, session: SessionDep
) -> list[ConversationRead]:
    rows = (
        await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
        )
    ).scalars()
    return [ConversationRead.model_validate(row) for row in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, user: CurrentUser, session: SessionDep
) -> ConversationDetail:
    conversation = await _load_conversation(session, user.id, conversation_id)
    messages = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    ).scalars()

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            MessageRead(
                id=message.id,
                role=message.role,  # type: ignore[arg-type]
                content=message.content,
                citations=[Citation(**c) for c in message.citations],
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: str, user: CurrentUser, session: SessionDep
) -> None:
    conversation = await _load_conversation(session, user.id, conversation_id)
    await session.execute(
        delete(Message).where(Message.conversation_id == conversation.id)
    )
    await session.delete(conversation)
    await session.commit()
