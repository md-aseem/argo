# ── Stage 1: build the chat UI ───────────────────────────────────────────────
FROM node:22-slim AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# ── Stage 2: the API, which also serves the built UI ─────────────────────────
FROM python:3.15-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# The app runs from src/ (not installed into site-packages), because it keeps
# the .adk state folder (local SQLite) next to the package.
COPY src ./src

# main.py serves this at /argo/ (it looks in <repo>/frontend/dist).
COPY --from=frontend /frontend/dist ./frontend/dist

# Run as a non-root user. With DATABASE_URL unset (e.g. a local docker run),
# conversations go to a SQLite file in src/.adk, so it must be writable by this
# user; mount a volume there to keep them. On GKE they're in Cloud SQL instead.
RUN useradd --uid 1000 --create-home argo \
    && mkdir -p /app/src/.adk \
    && chown argo /app/src/.adk
USER 1000

EXPOSE 8000

CMD ["uvicorn", "argo.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
