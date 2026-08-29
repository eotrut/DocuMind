# DocuMind — chat with your documents, with receipts

DocuMind is a full-stack retrieval-augmented generation (RAG) application. You
sign up, upload PDFs, and ask questions about them in a chat interface. Answers
are generated **only** from your own documents, and every claim carries an
inline citation you can click to see the exact source passage — the document,
the page, and the chunk the model actually read.

It is built to be read as well as run: a typed FastAPI backend with a pluggable
embedding layer, pgvector for similarity search, streaming responses over SSE,
and a 90-test suite that runs without Docker, a database server, or an API key.

```
Next.js 14 · TypeScript · Tailwind  ·  FastAPI · SQLAlchemy 2 (async) · pgvector
Google Gemini (chat + embeddings)   ·  JWT auth  ·  Docker Compose  ·  pytest
```

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [How the RAG pipeline works](#how-the-rag-pipeline-works)
- [Getting a free Gemini API key](#getting-a-free-gemini-api-key)
- [Run it with Docker (recommended)](#run-it-with-docker-recommended)
- [Run it locally without Docker](#run-it-locally-without-docker)
- [Environment variables](#environment-variables)
- [Testing](#testing)
- [Screenshots](#screenshots)
- [Design decisions worth defending](#design-decisions-worth-defending)
- [What I'd improve with more time](#what-id-improve-with-more-time)
- [Project layout](#project-layout)

---

## What it does

| Feature | Detail |
| --- | --- |
| **Accounts** | Email/password signup and login, bcrypt-hashed passwords, JWT bearer tokens. |
| **Ingestion** | Upload one or many PDFs. Each is parsed, chunked page-aware, embedded and stored as vectors scoped to your account. |
| **Grounded chat** | Your question is embedded, the top-*k* most similar chunks are retrieved, and Gemini answers from those chunks only. |
| **Streaming** | Answers stream token-by-token over Server-Sent Events. |
| **Inline citations** | The model cites `[1]`, `[2]`… Click a marker and a side panel shows the exact source text, filename, page number and relevance score. |
| **Document scoping** | Select specific documents in the sidebar to restrict retrieval to them. |
| **Chat history** | Conversations and messages persist per user, with their citations. |
| **Rate-limit handling** | Gemini 429/5xx responses are retried with exponential backoff (honouring the server's own `retryDelay` hint); a persistent limit surfaces as a clear message, not a crash. |
| **Graceful failure** | Bad PDFs, scanned PDFs with no text layer, oversized files, empty questions and expired tokens all produce specific, actionable errors. |

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI["Next.js 14 App Router<br/>TypeScript · Tailwind"]
    end

    subgraph API["FastAPI (async)"]
        AUTH["auth router<br/>JWT · bcrypt"]
        DOCS["documents router"]
        CHAT["chat router<br/>SSE stream"]
        ING["ingestion service<br/>parse → chunk → embed"]
        RET["retrieval service<br/>cosine top-k"]
        RAG["RAG service<br/>prompt · citations"]
    end

    subgraph Data["PostgreSQL 16 + pgvector"]
        T1[("users · documents")]
        T2[("chunks + vector(768)<br/>HNSW index")]
        T3[("conversations · messages")]
    end

    GEM["Google Gemini API<br/>gemini-3.6-flash<br/>gemini-embedding-001"]

    UI -- "REST + JWT" --> AUTH
    UI -- "multipart upload" --> DOCS
    UI -- "POST /chat/stream (SSE)" --> CHAT
    DOCS --> ING
    ING -- "embed chunks" --> GEM
    ING --> T2
    CHAT --> RET --> T2
    CHAT --> RAG -- "question + sources" --> GEM
    GEM -- "streamed tokens" --> CHAT
    AUTH --> T1
    CHAT --> T3
```

### Request flow for one question

```
 user question
      │
      ├─► embed query (Gemini, task_type=RETRIEVAL_QUERY)
      │
      ├─► pgvector:  SELECT … ORDER BY embedding <=> :q LIMIT 5
      │              WHERE user_id = :me          ← ownership enforced in SQL
      │
      ├─► SSE "sources"  ─────────────────────────► UI renders citation targets
      │
      ├─► Gemini: system prompt + numbered SOURCES + question
      │
      ├─► SSE "token" × N ────────────────────────► UI streams the answer,
      │                                              turning [n] into chips
      └─► SSE "done" (markers actually cited) ────► UI keeps only used sources
                                                    and persists the exchange
```

---

## How the RAG pipeline works

**Chunking** ([`backend/app/services/chunking.py`](backend/app/services/chunking.py))
Text is split into *units* — paragraphs first, then sentences for over-long
paragraphs, then word windows as a last resort — and units are packed into
~1000-character chunks with ~180 characters of overlap. Packing whole units
means chunks rarely end mid-sentence, which improves both embedding quality and
how quotable a citation is. Chunks pack *across* page boundaries (strict
per-page chunking produces thin, useless vectors on title and reference pages)
but each chunk records the page it starts on, so citations still point at a real
page. Fragments below 120 characters are folded back into their predecessor.

**Embeddings** ([`backend/app/services/embeddings.py`](backend/app/services/embeddings.py))
Three interchangeable providers behind one interface, selected by
`EMBEDDING_PROVIDER`:

| Provider | Model | Dims | Cost | Notes |
| --- | --- | --- | --- | --- |
| `gemini` *(default)* | `gemini-embedding-001` | 768 | Free tier | Requested at 768 dims (the model is Matryoshka-trained) and re-normalised, as Google requires for any non-native dimensionality. |
| `local` | `all-MiniLM-L6-v2` | 384 | Free, offline | Zero quota usage; costs a ~2 GB PyTorch install. `pip install -r backend/requirements-local-embeddings.txt` |
| `fake` | hashed bag-of-words | 256 | Free, offline | Used by the test-suite. It is a genuine lexical embedding, so retrieval tests assert on real ranking behaviour rather than mocks. |

> **Why Gemini embeddings by default?** They are free-tier eligible, need no
> model download, and keep the Docker image small. The local model is a
> first-class fallback for anyone who would rather not spend API quota — one env
> var switches it. Document/query embeddings use the asymmetric
> `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task types, and all vectors are
> L2-normalised so cosine similarity is a plain dot product.

**Retrieval** ([`backend/app/services/retrieval.py`](backend/app/services/retrieval.py))
On PostgreSQL, ranking happens in the database with pgvector's `<=>`
cosine-distance operator, backed by an HNSW index (`m=16, ef_construction=64`).
Scores are reported as `1 - distance`, i.e. cosine similarity in `[-1, 1]`.
The `user_id` filter lives in the SQL itself, so a leaked document id still
cannot surface another user's text.

**Generation** ([`backend/app/services/rag.py`](backend/app/services/rag.py))
The model receives a system prompt that forbids outside knowledge, requires a
`[n]` marker after every factual claim, and requires it to say *"I could not
find that in your documents"* rather than guess. Markers the model invents
beyond the supplied source list are discarded server-side before they ever reach
the UI. Temperature is 0.2 — this is extraction, not creative writing.

---

## Getting a free Gemini API key

1. Go to **[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)**.
2. Sign in with a Google account.
3. Click **Create API key** (you can create it in a new project).
4. Copy the key and paste it into your `.env` as `GEMINI_API_KEY=…`.

No billing details are required for the free tier.

**Free-tier quotas are per-minute *and* per-day, and each model has its own
bucket.** Chat and embedding quotas are counted separately too, so it is normal
to see generation rate-limited while ingestion still works. When the backend
exhausts its retries it reports a clean HTTP 429 and the UI shows *"The Gemini
free tier is rate-limited right now"* rather than failing:

```
Gemini generation hit a rate limit; retrying in 20.3s (attempt 1/4)
Gemini generation hit a rate limit; retrying in 30.4s (attempt 2/4)
Gemini generation hit a rate limit; retrying in 28.4s (attempt 3/4)
Chat stream ended with a handled error: The Gemini free tier is rate-limited …
```

If you burn a model's **daily** quota, the fastest fix is to point
`GEMINI_MODEL` at a different flash model — the buckets are independent — and
restart the backend (`docker compose up -d --force-recreate backend`). No code
change is needed; that is the whole reason the model name is an env var.

**Model names move.** `GEMINI_MODEL` and `GEMINI_EMBEDDING_MODEL` are env vars
precisely so you never have to touch code when Google retires a model. If you
see a `404 … no longer available`, list what your key can actually reach:

```bash
python - <<'PY'
import asyncio, os
from google import genai
async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    async for m in await client.aio.models.list(config={"page_size": 200}):
        if "generateContent" in (m.supported_actions or []): print(m.name)
asyncio.run(main())
PY
```

---

## Run it with Docker (recommended)

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2).

```bash
git clone <your-repo-url> documind
cd documind

cp .env.example .env
# Edit .env and set at minimum:
#   GEMINI_API_KEY=<your key from AI Studio>
#   JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">

docker compose up --build
```

Then open **<http://localhost:3000>**.

| Service | URL | Notes |
| --- | --- | --- |
| Frontend | <http://localhost:3000> | Next.js |
| Backend | <http://localhost:8000> | FastAPI |
| API docs | <http://localhost:8000/docs> | Interactive OpenAPI |
| Health | <http://localhost:8000/health> | Also the Compose healthcheck |
| Postgres | `localhost:5433` | Mapped off 5432 to avoid colliding with a local Postgres |

Compose overrides `DATABASE_URL` with the in-network DSN, so a `.env` left
pointing at `localhost` cannot break the containerised stack. Tables and the
pgvector extension are created automatically on first boot.

```bash
docker compose down          # stop
docker compose down -v       # stop and delete the database volume
docker compose logs -f backend
```

---

## Run it locally without Docker

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# From the repo root, cp .env.example .env and fill in GEMINI_API_KEY.
# For a zero-infrastructure run, set:
#   DATABASE_URL=sqlite+aiosqlite:///./documind.db

uvicorn app.main:app --reload --port 8000
```

> **SQLite fallback.** The app runs on either PostgreSQL+pgvector or SQLite. On
> SQLite, similarity search is computed in NumPy in-process instead of in the
> database. It is there so the test-suite and a quick local run need no
> infrastructure at all — it is *not* meant for large corpora. Point
> `DATABASE_URL` at Postgres for anything real.

To use the Compose database from a host-run backend:

```bash
docker compose up -d db
# DATABASE_URL=postgresql+asyncpg://documind:documind@localhost:5433/documind
```

### Frontend

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev        # http://localhost:3000
```

### End-to-end smoke test

With the backend running and a real key in `.env`:

```bash
cd backend
python scripts/smoke_test.py
```

It signs up a throwaway user, uploads a synthetic PDF, asks a question, and
asserts that the streamed answer cites a resolvable source containing the
expected figure — then cleans up.

---

## Environment variables

Copy `.env.example` to `.env`. Everything has a working default except the
Gemini key.

| Variable | Default | What it does |
| --- | --- | --- |
| `GEMINI_API_KEY` | *(empty — **you must set this**)* | Free key from Google AI Studio. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Chat/generation model. `gemini-flash-latest` if you want it to auto-track. |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model. |
| `EMBEDDING_PROVIDER` | `gemini` | `gemini` · `local` · `fake`. |
| `EMBEDDING_DIM` | `768` | Must match the provider (768/384/256). Validated at boot; changing it requires re-ingesting documents. |
| `DATABASE_URL` | Compose DSN | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///./documind.db`. |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `documind` | Consumed by the Postgres container. |
| `JWT_SECRET` | `change-me-in-production` | **Change this.** A weak value logs a warning at boot. |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `180` | Characters. Overlap must be smaller than size. |
| `RETRIEVAL_TOP_K` | `5` | Chunks sent to the model per question. |
| `MAX_UPLOAD_MB` | `20` | Per-file upload ceiling. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allow-list. |
| `LOG_LEVEL` | `INFO` | |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Baked into the browser bundle at build time. |

`.env` is git-ignored. Only `.env.example` is committed.

---

## Testing

Every push runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml): ruff
lint + format check, the pytest suite, eslint, `tsc --noEmit`, `next build`, and
a build of both Docker images.

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest              # 90 tests, ~10s, no Docker / DB / API key needed
```

The suite uses a throwaway SQLite file and the deterministic `fake` embedding
provider, so it is hermetic and fast. Gemini is never called: the chat tests
substitute the stream with a local generator, which leaves retrieval, citation
resolution, SSE framing and persistence all exercised for real.

| File | Covers |
| --- | --- |
| `test_chunking.py` | Size budget, overlap across the seam, page attribution, de-hyphenation, scrap merging, guard rails. |
| `test_pdf_and_embeddings.py` | Page extraction, rejection of non-PDFs / empty files / scans without a text layer, embedding determinism, normalisation and ranking. |
| `test_retrieval.py` | Ranking order, `top_k`, score floor, document filtering, and that search never crosses user boundaries. |
| `test_auth_api.py` | Signup, duplicate email, weak password, login, non-enumerable failures, garbage/expired/orphaned tokens. |
| `test_documents_api.py` | Upload → chunk persistence, partial-batch failure, per-file errors, cascade delete of vectors, cross-user 404s. |
| `test_chat_api.py` | Full SSE protocol, citation resolution and persistence, follow-ups, document filtering, empty query, auth. |
| `test_rag.py` | Prompt assembly, snippet truncation, marker de-duplication, discarding hallucinated markers. |
| `test_gemini_retry.py` | Backoff growth, `retryDelay` hint, 429 → `RateLimitedError`, non-retryable 4xx, missing key. |
| `test_rag_streaming.py` | The lazy-connect edge: a 429 or dropped connection on the *first* chunk is retried as a connection failure, while a drop after tokens have flowed is a truncated answer that must not silently restart. |
| `test_postgres_dialect.py` | Compiles the pgvector query and DDL against the PostgreSQL dialect — `VECTOR(n)` column rendering, the distance cast, owner scoping and expanding `IN` — so the production path is checked even though the suite runs on SQLite. |

---

## Screenshots

> _Placeholder — drop images into `docs/screenshots/` and they will render here._

| | |
| --- | --- |
| **Sign in** — `docs/screenshots/login.png` | **Workspace** — `docs/screenshots/chat.png` |
| **Citation panel** — `docs/screenshots/citation.png` | **Document sidebar** — `docs/screenshots/documents.png` |

```markdown
![Workspace](docs/screenshots/chat.png)
```

---

## Design decisions worth defending

**Citations are resolved server-side, before the first token.** The `sources`
SSE event carries every retrieved chunk with its marker, filename, page and
snippet. The UI can therefore turn a `[2]` into a working popover the instant it
streams in, instead of waiting for the answer to finish and then re-fetching.

**Only *used* sources are kept.** When the stream ends, the backend reports which
markers the model actually cited and stores just those on the message, so the
"Sources" footer reflects what the answer relied on rather than what retrieval
happened to return.

**Ownership is enforced in SQL, not in Python.** Every vector query filters on
`user_id` inside the statement. There is a test that asserts a second user
retrieves nothing from the first user's documents.

**Errors mid-stream are `error` events, not HTTP statuses.** Once the response
headers are on the wire a status code cannot be changed, so failures after
streaming has begun arrive as a typed event the UI renders inline. Failures
*before* streaming (retrieval, rate limits) still return real status codes.

**Ingestion is synchronous.** The upload response already carries the document's
final state, so the UI never polls. The trade-off — a large PDF holds the
request open — is listed below as the first thing I'd change.

**Schema creation, not migrations.** The schema is five tables for a
single-service project; Alembic would be ceremony. Also listed below.

---

## What I'd improve with more time

1. **Background ingestion.** Move parse/chunk/embed to a task queue (ARQ or
   Celery + Redis) and stream progress over WebSocket. Today a 200-page PDF
   holds the upload request open for the duration.
2. **Alembic migrations.** `create_all` at boot is fine for a demo and wrong for
   anything that has to evolve a live schema.
3. **Hybrid retrieval + reranking.** Dense vectors alone miss exact-match
   queries (invoice numbers, proper nouns). I'd add Postgres full-text search,
   fuse with Reciprocal Rank Fusion, and rerank the union with a cross-encoder.
4. **Tokens in an httpOnly cookie.** The JWT currently lives in `localStorage`,
   which is readable by injected scripts. A `SameSite=Strict`, httpOnly cookie
   plus refresh-token rotation is the right answer.
5. **Per-user rate limiting and quotas.** Right now one enthusiastic user can
   exhaust the shared free-tier quota for everyone.
6. **OCR for scanned PDFs.** Detect a missing text layer and route through
   Tesseract instead of rejecting the upload.
7. **Answer-quality evaluation.** A small golden set of question/answer/expected
   -source triples scored for retrieval hit-rate and citation precision, run in
   CI, so prompt or chunking changes can be judged rather than guessed at.
8. **Frontend tests.** The backend has 90 tests; the UI has none. Vitest for the
   citation parser and a Playwright run through signup → upload → cited answer.
9. **Observability.** Structured JSON logs, OpenTelemetry traces spanning
   retrieval and generation, and per-request token accounting.
10. **Deeper CI.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) already
    runs ruff, pytest, eslint, `tsc --noEmit`, `next build` and both Docker
    builds on every push. Next would be mypy in strict mode and a Compose
    smoke test that boots the stack and runs `scripts/smoke_test.py` against it.

---

## Project layout

```
.
├── backend/
│   ├── app/
│   │   ├── core/            # settings, async engine, security, error types
│   │   │   ├── config.py    #   env-driven Settings + provider dim validation
│   │   │   ├── database.py  #   engine, session factory, schema + HNSW bootstrap
│   │   │   ├── security.py  #   bcrypt hashing, JWT issue/verify
│   │   │   └── errors.py    #   domain errors → HTTP statuses
│   │   ├── routers/         # HTTP surface
│   │   │   ├── auth.py      #   /api/auth/{signup,login,me}
│   │   │   ├── documents.py #   /api/documents CRUD + chunk inspection
│   │   │   └── chat.py      #   /api/chat/stream (SSE) + conversations
│   │   ├── services/        # business logic, framework-free
│   │   │   ├── pdf.py       #   pypdf extraction + failure modes
│   │   │   ├── chunking.py  #   page-aware unit packing with overlap
│   │   │   ├── embeddings.py#   gemini | local | fake providers
│   │   │   ├── retrieval.py #   pgvector and NumPy search backends
│   │   │   ├── rag.py       #   prompt assembly, citations, streaming
│   │   │   ├── ingestion.py #   upload → ready/failed pipeline
│   │   │   └── gemini_client.py  # shared client, backoff, error translation
│   │   ├── models.py        # SQLAlchemy ORM + dialect-aware vector column
│   │   ├── schemas.py       # Pydantic request/response contracts
│   │   ├── dependencies.py  # auth + session + provider injection
│   │   └── main.py          # app factory, CORS, lifespan, error handlers
│   ├── scripts/smoke_test.py
│   ├── tests/               # 73 tests
│   ├── requirements.txt · requirements-dev.txt · requirements-local-embeddings.txt
│   └── pyproject.toml       # pytest + ruff config
├── frontend/
│   ├── app/                 # App Router: /, /login, /signup, /chat
│   ├── components/          # AuthForm, DocumentSidebar, UploadDropzone,
│   │                        # ChatPanel, AnswerText, SourcePanel, Icons
│   ├── lib/                 # api client (incl. SSE parser), auth context, types
│   └── tailwind.config.ts
├── docker/
│   ├── backend.Dockerfile   # python:3.12-slim, non-root
│   └── frontend.Dockerfile  # multi-stage, standalone output, non-root
├── docker-compose.yml       # db (pgvector/pg16) + backend + frontend
├── .env.example
└── README.md
```
