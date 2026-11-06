"""Create and serve the Argo HTTP API and chat UI.

Everything is served under /argo (API at /argo/api, UI at /argo/), both locally
and in production, so the app behaves the same behind muhammadaseem.com/argo/.

Run from the repo root:

    .venv/bin/uvicorn argo.main:app --app-dir src --reload --reload-dir src --port 8000
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from .routes import router

BASE_PATH = "/argo"

# Same layout locally (<repo>/frontend/dist) and in the Docker image (/app/frontend/dist).
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="Argo")
app.include_router(router, prefix=BASE_PATH)


@app.get("/", include_in_schema=False)
@app.get(BASE_PATH, include_in_schema=False)
async def to_ui() -> RedirectResponse:
    return RedirectResponse(f"{BASE_PATH}/")


# The built UI. Registered after the API routes so /argo/api/* always reaches
# the API. Only present once `npm run build` has produced frontend/dist.
if DIST.is_dir():

    @app.get(BASE_PATH + "/{path:path}", include_in_schema=False)
    async def ui(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)  # an unknown API route, not a page
        file = (DIST / path).resolve()
        if path and file.is_file() and file.is_relative_to(DIST):
            # Vite fingerprints everything under assets/, so it can be cached forever.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        if "." in Path(path).name:
            raise HTTPException(404)  # a missing file, not a client-side route
        # Any other path is a client-side route: hand it the app shell.
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})


def main() -> None:
    """Entry point for the `argo` console script: serve on port 8000."""
    import uvicorn

    uvicorn.run("argo.main:app", host="127.0.0.1", port=8000)
