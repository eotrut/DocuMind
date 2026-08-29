"""Pluggable embedding providers.

Three implementations sit behind one interface, chosen by ``EMBEDDING_PROVIDER``:

``gemini``
    ``gemini-embedding-001`` via the Gemini API. Free-tier eligible, no local
    model download, and it is the default. Vectors are requested at 768
    dimensions (the model is Matryoshka-trained, so truncation is lossy-but-sane)
    and re-normalised afterwards, which Google requires for any output
    dimensionality other than the native 3072.

``local``
    ``sentence-transformers/all-MiniLM-L6-v2`` on-device: zero API cost and zero
    quota, at the price of a ~2 GB PyTorch dependency. Install it with
    ``pip install -r requirements-local-embeddings.txt``.

``fake``
    Deterministic hashed bag-of-words, used by the test-suite. It is a real
    lexical embedding (similar text produces similar vectors), so retrieval
    tests assert on genuine ranking behaviour rather than on mocks.

Document and query embeddings are both L2-normalised, which makes cosine
similarity equal to a dot product and keeps the pgvector and NumPy code paths
numerically identical.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache

from google.genai import types as genai_types

from app.core.config import PROVIDER_DIMENSIONS, settings
from app.services.gemini_client import get_client, with_retries

logger = logging.getLogger(__name__)

#: Gemini rejects very large embedding batches; this also keeps retries cheap.
GEMINI_BATCH_SIZE = 32


def l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


class EmbeddingProvider(ABC):
    """Turns text into normalised vectors of :attr:`dimension` floats."""

    name: str
    dimension: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed corpus text (indexing side)."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a user question (search side)."""


class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"

    def __init__(self, model: str, dimension: int) -> None:
        self.model = model
        self.dimension = dimension

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        client = get_client()
        config = genai_types.EmbedContentConfig(
            task_type=task_type, output_dimensionality=self.dimension
        )

        async def call() -> list[list[float]]:
            response = await client.aio.models.embed_content(
                model=self.model, contents=list(texts), config=config
            )
            return [list(e.values or []) for e in (response.embeddings or [])]

        vectors = await with_retries(call, what="Gemini embedding")
        if len(vectors) != len(texts):
            raise ValueError(
                f"Gemini returned {len(vectors)} embeddings for {len(texts)} inputs"
            )
        return [l2_normalise(v) for v in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), GEMINI_BATCH_SIZE):
            batch = texts[start : start + GEMINI_BATCH_SIZE]
            out.extend(await self._embed(batch, "RETRIEVAL_DOCUMENT"))
        return out

    async def embed_query(self, text: str) -> list[float]:
        # RETRIEVAL_QUERY vs RETRIEVAL_DOCUMENT is asymmetric on purpose: the
        # model projects questions and passages into a shared space.
        vectors = await self._embed([text], "RETRIEVAL_QUERY")
        return vectors[0]


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers, executed in a worker thread (the model is sync)."""

    name = "local"
    MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self._model = None
        self._lock = asyncio.Lock()

    async def _get_model(self):
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:  # pragma: no cover - env dependent
                        raise RuntimeError(
                            "EMBEDDING_PROVIDER=local requires sentence-transformers: "
                            "pip install -r requirements-local-embeddings.txt"
                        ) from exc
                    logger.info("Loading local embedding model %s", self.MODEL_ID)
                    self._model = await asyncio.to_thread(
                        SentenceTransformer, self.MODEL_ID
                    )
        return self._model

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        model = await self._get_model()
        vectors = await asyncio.to_thread(
            model.encode, texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._encode(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._encode([text]))[0]


#: Very common words carry no retrieval signal, and at low dimensionality they
#: dominate the hashed vector - "what was X in 2024" would otherwise match any
#: sentence containing "was" and "2024". Real embedding models learn to discount
#: them; the hashing trick has to be told.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "much",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
        "you",
        "your",
    ]
)


class FakeEmbeddingProvider(EmbeddingProvider):
    """Hashed bag-of-words embeddings: deterministic, offline, dependency-free.

    Each token is hashed into a bucket with a sign, mirroring the classic
    "hashing trick". Sub-word trigrams are added at a lower weight so that
    morphological variants (invoice / invoices) still land near each other.
    """

    name = "fake"
    TRIGRAM_WEIGHT = 0.25

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        content_tokens = [t for t in tokens if t not in _STOPWORDS]
        # Fall back to the raw tokens rather than emitting a zero vector for
        # input that is nothing but stopwords.
        for token in content_tokens or tokens:
            self._add(vector, token, 1.0)
            for i in range(len(token) - 2):
                self._add(vector, token[i : i + 3], self.TRIGRAM_WEIGHT)
        return l2_normalise(vector)

    def _add(self, vector: list[float], key: str, weight: float) -> None:
        digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        bucket = value % self.dimension
        sign = 1.0 if (value >> 63) & 1 else -1.0
        vector[bucket] += sign * weight

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def build_provider(
    name: str | None = None, dimension: int | None = None
) -> EmbeddingProvider:
    name = name or settings.embedding_provider
    dimension = dimension or settings.embedding_dim
    if name == "gemini":
        return GeminiEmbeddingProvider(settings.gemini_embedding_model, dimension)
    if name == "local":
        return LocalEmbeddingProvider(dimension)
    if name == "fake":
        return FakeEmbeddingProvider(dimension)
    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER {name!r}; "
        f"expected one of {sorted(PROVIDER_DIMENSIONS)}"
    )


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    provider = build_provider()
    logger.info("Embeddings: provider=%s dimension=%d", provider.name, provider.dimension)
    return provider
