"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models import User
from app.services.embeddings import EmbeddingProvider, get_embedding_provider

# auto_error=False so a missing header produces our own 401 shape rather than
# FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_UNAUTHORISED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Your session has expired. Please sign in again.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Resolve the bearer token to a live user, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORISED

    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise _UNAUTHORISED

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        # Token is well-formed but the account is gone (deleted between requests).
        raise _UNAUTHORISED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_provider() -> EmbeddingProvider:
    return get_embedding_provider()


ProviderDep = Annotated[EmbeddingProvider, Depends(get_provider)]
