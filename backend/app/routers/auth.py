"""Email/password signup and login, issuing JWT bearer tokens."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password, verify_password
from app.dependencies import CurrentUser, SessionDep
from app.models import User
from app.schemas import TokenResponse, UserCreate, UserLogin, UserRead

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalise_email(email: str) -> str:
    return email.strip().lower()


@router.post(
    "/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def signup(payload: UserCreate, session: SessionDep) -> TokenResponse:
    email = _normalise_email(payload.email)

    exists = (
        await session.execute(select(User.id).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id), user=UserRead.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLogin, session: SessionDep) -> TokenResponse:
    email = _normalise_email(payload.email)
    user = (
        await session.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()

    # Same message and code for "no such user" and "wrong password": leaking
    # which one it was would let an attacker enumerate registered emails.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    return TokenResponse(
        access_token=create_access_token(user.id), user=UserRead.model_validate(user)
    )


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
