"""Create and serve the Argo HTTP API.

Run from the repo root:

    .venv/bin/uvicorn argo.main:app --app-dir src --reload --reload-dir src --port 8000
"""

from fastapi import FastAPI

from .routes import router

app = FastAPI(title="Argo")
app.include_router(router)


def main() -> None:
    """Entry point for the `argo` console script: serve the API on port 8000."""
    import uvicorn

    uvicorn.run("argo.main:app", host="127.0.0.1", port=8000)
