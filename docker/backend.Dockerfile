# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# DocuMind backend - FastAPI + Uvicorn
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements are copied first so dependency layers are cached across code edits.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/

# Run as a non-root user: the container never needs to write outside /tmp.
RUN useradd --create-home --uid 10001 documind && chown -R documind:documind /app
USER documind

EXPOSE 8000

# Single worker on purpose: free-tier Gemini quota is the bottleneck, not CPU,
# and the app is fully async. Scale with replicas rather than workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
