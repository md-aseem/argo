# Argo

Argo is Aseem's personal AI agent: a Google ADK agent (`src/argo/agent.py`) with a chat UI.

```
src/argo/   the agent (agent.py), API routes (routes.py), and entry point (main.py)
frontend/   the chat UI: Vite + React + TypeScript
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python 3.15 (see `.python-version`; uv fetches it if needed)
- Node.js 20.19+ or 22.12+, and npm
- Google Cloud access for Vertex AI. Sign in once with `gcloud auth application-default login`.
- `.env` at the repo root (git-ignored): `cp .env.example .env` and fill in `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_BUCKET`. That's all a local run needs; the rest of the file is used by the production scripts. Conversations go to a local SQLite file unless you set `DATABASE_URL`.

## Set up (first time)

From the repo root:

```sh
uv sync                        # Python dependencies into .venv
cd frontend && npm install     # frontend dependencies
```

## Start the app (development)

Two terminals, both from the repo root.

**1. API** on port 8000:

```sh
.venv/bin/uvicorn argo.main:app --app-dir src --reload --reload-dir src --port 8000
```

**2. Chat UI** on port 5173, with hot reload. It proxies `/argo/api` to the API.

```sh
cd frontend
npm run dev
```

Open http://localhost:5173/argo/. Argo is served under `/argo/` everywhere, so it behaves the same locally as at muhammadaseem.com/argo/.

If the API isn't on port 8000, start the UI with `ARGO_API=http://127.0.0.1:<port> npm run dev`.

## Build and start (production build)

**1. Build the UI.** This type-checks, then writes the bundle to `frontend/dist`.

```sh
cd frontend
npm run build
```

**2. Start the API** (no `--reload`), from the repo root. It serves the built UI itself.

```sh
.venv/bin/uvicorn argo.main:app --app-dir src --port 8000
```

Open http://localhost:8000/argo/. To stop the server, press Ctrl+C.

(`npm run preview` in `frontend/` also works for a static preview on port 4173; it proxies `/argo/api` to the API.)

## Run with Docker

The [Dockerfile](Dockerfile) builds the chat UI and packages it with the API in one image, which runs as a non-root user. It needs the same `.env` and Google credentials as above, but neither is baked into the image (both are in `.dockerignore`), so you pass them in when you run it.

**1. Build the image**, from the repo root:

```sh
docker build -t argo .
```

**2. Run it** on port 8000:

```sh
docker run --rm -p 8000:8000 \
  --env-file .env \
  -v "$HOME/.config/gcloud:/home/argo/.config/gcloud:ro" \
  -v argo-data:/app/src/.adk \
  argo
```

- `--env-file .env` supplies the `GOOGLE_*` settings. Write the values without quotes, because Docker keeps quotes literally.
- The `gcloud` mount shares the credentials from `gcloud auth application-default login`. Skip it if you run somewhere with its own service account (Cloud Run, GCE, GKE).
- The `argo-data` volume keeps conversations (`argo.db`) across container restarts. Leave it out for throwaway runs.

Open http://localhost:8000/argo/. To stop the container, press Ctrl+C.

## Production

Argo is live at https://muhammadaseem.com/argo/, on GKE behind a Google login, with conversations in Cloud SQL (Postgres). To ship a change:

```sh
uvx bump2version patch     # or minor / major; bumps the version, commits, tags
git push --follow-tags     # CI builds and pushes the image
scripts/deploy.sh          # roll it out
```

How it's put together, what's running where, and what to check when something breaks: [docs/production.md](docs/production.md).

## How it fits together

- **Conversations** are stored in Postgres when `DATABASE_URL` is set (production), otherwise in `src/.adk/argo.db` (git-ignored). The open one is in the URL as `?s=<id>`, so a reload keeps it. The New chat button in the sidebar starts a new chat.
- **Replies stream** over server-sent events from `POST /argo/api/chat`. The API turns ADK events into `text`, `tool`, `tool_result`, `artifact`, `error` and `done`; the UI renders them as they arrive.
- **Tools**: any tool you add to the agent shows up in the reply header (`read_file · write_file ›`, click to expand) and in the status line while it runs.
- **Files Argo saves** (ADK artifacts, via `tool_context.save_artifact`) appear as rows under the reply with a VIEW link. They are stored in the GCS bucket named by `GOOGLE_CLOUD_BUCKET` (see below).
- **Attachments**: PDFs, images, audio and video are sent to the model as-is; any other file must be UTF-8 text. 15 MB total per message. Each upload is also saved to the artifact bucket.
- **Artifact storage**: files the agent saves and files you attach are kept in the GCS bucket `GOOGLE_CLOUD_BUCKET` (the API won't start without it). Objects are laid out as `<app>/<user>/<session>/<filename>/<version>`, where `<app>` is `src`, `<user>` is the signed-in Google email in production (`aseem` locally, see `LOCAL_USER_ID`), `<session>` is the conversation id (the `?s=` value in the URL) and `<version>` starts at `0`; saving the same filename again in a session adds a new version. Your Google credentials need write access to the bucket.

## API

| Route | |
| --- | --- |
| `POST /argo/api/chat` | `{session_id?, text, attachments[]}` → SSE stream. Omit `session_id` to start a conversation. |
| `GET /argo/api/sessions` | All conversations, newest first. |
| `GET /argo/api/sessions/{id}` | One conversation with its messages. |
| `GET /argo/api/sessions/{id}/artifacts/{name}` | A file the agent saved. |
| `GET /argo/api/health` | Health check (used by Kubernetes). |
