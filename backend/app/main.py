"""DocuMind FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_models
from app.core.errors import DocuMindError, RateLimitedError
from app.routers import auth, chat, documents

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("documind")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_embedding_dim()
    if settings.jwt_secret == "change-me-in-production" or len(settings.jwt_secret) < 32:
        logger.warning(
            "JWT_SECRET is weak or left at its default. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if not settings.gemini_api_key:
        logger.warning(
            "GEMINI_API_KEY is not set - uploads and chat will fail until it is. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
    await init_models()
    logger.info(
        "DocuMind API ready (db=%s, embeddings=%s/%dd, model=%s)",
        "postgres" if settings.is_postgres else "sqlite",
        settings.embedding_provider,
        settings.embedding_dim,
        settings.gemini_model,
    )
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Chat with your PDFs: retrieval-augmented answers with citations.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DocuMindError)
async def documind_error_handler(_: Request, exc: DocuMindError) -> JSONResponse:
    """Render domain errors as clean JSON the frontend can display verbatim."""
    headers = {}
    if isinstance(exc, RateLimitedError) and exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=headers
    )


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe, also used by the Compose healthcheck."""
    return {
        "status": "ok",
        "model": settings.gemini_model,
        "embedding_provider": settings.embedding_provider,
        "gemini_key_configured": bool(settings.gemini_api_key),
    }


app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
