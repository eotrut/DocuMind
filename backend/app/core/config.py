"""Application settings, loaded from the environment (or a local `.env`)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingProvider = Literal["gemini", "local", "fake"]

#: Output dimensionality of each supported embedding provider. Used to validate
#: EMBEDDING_DIM at boot so a mismatch fails loudly instead of silently
#: corrupting the vector index.
PROVIDER_DIMENSIONS: dict[str, int] = {
    "gemini": 768,
    "local": 384,
    "fake": 256,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "DocuMind API"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./documind.db"

    # --- Auth --------------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # --- Gemini ------------------------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    # --- Embeddings --------------------------------------------------------
    embedding_provider: EmbeddingProvider = "gemini"
    embedding_dim: int = 768

    # --- Ingestion / retrieval --------------------------------------------
    chunk_size: int = Field(default=1000, ge=200, le=8000)
    chunk_overlap: int = Field(default=180, ge=0, le=2000)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    max_upload_mb: int = Field(default=20, ge=1, le=100)

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_smaller_than_chunk(cls, v: int, info) -> int:
        chunk_size = info.data.get("chunk_size", 1000)
        if v >= chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate_embedding_dim(self) -> None:
        """Fail fast when EMBEDDING_DIM contradicts EMBEDDING_PROVIDER."""
        expected = PROVIDER_DIMENSIONS[self.embedding_provider]
        if self.embedding_dim != expected:
            raise ValueError(
                f"EMBEDDING_DIM={self.embedding_dim} does not match provider "
                f"'{self.embedding_provider}' (expects {expected}). Update the "
                "env var and re-ingest your documents."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
