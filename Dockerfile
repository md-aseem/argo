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
# .env and the .adk state folder next to the package.
COPY src ./src

EXPOSE 8000

CMD ["uvicorn", "argo.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
