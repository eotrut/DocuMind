"""Pydantic request/response models — the API's typed contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# --------------------------------------------------------------------------- auth
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead


# ---------------------------------------------------------------------- documents
class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    size_bytes: int
    page_count: int
    chunk_count: int
    status: Literal["processing", "ready", "failed"]
    error: str | None = None
    created_at: datetime


class UploadResult(BaseModel):
    """Per-file outcome of a multi-file upload — partial success is normal."""

    filename: str
    ok: bool
    document: DocumentRead | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    chunk_index: int
    page_number: int
    content: str


# --------------------------------------------------------------------------- chat
class Citation(BaseModel):
    """One retrieved chunk, as referenced by a ``[n]`` marker in the answer."""

    marker: int = Field(description="The n in the [n] marker used in the answer")
    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_index: int
    score: float = Field(description="Cosine similarity in [-1, 1]; higher is better")
    snippet: str


class MessageRead(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    document_ids: list[str] | None = Field(
        default=None, description="Restrict retrieval to these documents"
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


class ErrorResponse(BaseModel):
    detail: str
