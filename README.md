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
- `src/.env` (git-ignored) with:
  ```
  GOOGLE_GENAI_USE_ENTERPRISE=...
  GOOGLE_CLOUD_PROJECT=...
  GOOGLE_CLOUD_LOCATION=...
  GOOGLE_CLOUD_BUCKET=...   # GCS bucket where artifacts are saved/loaded (required)
  ```

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

**2. Chat UI** on port 5173, with hot reload. It proxies `/api` to the API.

```sh
cd frontend
npm run dev
```

Open http://localhost:5173.

If the API isn't on port 8000, start the UI with `ARGO_API=http://127.0.0.1:<port> npm run dev`.

## Build and start (production build)

**1. Build the UI.** This type-checks, then writes the bundle to `frontend/dist`.

```sh
cd frontend
npm run build
```

**2. Start the API** (no `--reload`), from the repo root:

```sh
.venv/bin/uvicorn argo.main:app --app-dir src --port 8000
```

**3. Serve the build** on port 4173, proxying `/api` to the API:

```sh
cd frontend
npm run preview
```

Open http://localhost:4173.

`npm run preview` is a local static server for trying the build. The API does not serve `frontend/dist` itself, so to host Argo elsewhere, put any static host in front of `dist` and route `/api` to the API.

To stop either server, press Ctrl+C in its terminal.

## Run the API with Docker

The [Dockerfile](Dockerfile) packages the API only (not the chat UI). It needs the same `src/.env` and Google credentials as above, but neither is baked into the image (both are in `.dockerignore`), so you pass them in when you run it.

**1. Build the image**, from the repo root:

```sh
docker build -t argo .
```

**2. Run it** on port 8000:

```sh
docker run --rm -p 8000:8000 \
  --env-file src/.env \
  -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
  -v argo-data:/app/src/.adk \
  argo
```

- `--env-file src/.env` supplies the `GOOGLE_*` settings. Write the values without quotes, because Docker keeps quotes literally.
- The `gcloud` mount shares the credentials from `gcloud auth application-default login`. Skip it if you run somewhere with its own service account (Cloud Run, GCE).
- The `argo-data` volume keeps conversations (`argo.db`) across container restarts. Leave it out for throwaway runs.

**3. Start the chat UI** against the container, in a second terminal. The UI step is the same as in development:

```sh
cd frontend
npm run dev
```

Open http://localhost:5173. To stop the container, press Ctrl+C.

## How it fits together

- **Conversations** are stored in `src/.adk/argo.db` (git-ignored). The open one is in the URL as `?s=<id>`, so a reload keeps it. The New chat button in the sidebar starts a new chat.
- **Replies stream** over server-sent events from `POST /api/chat`. The API turns ADK events into `text`, `tool`, `tool_result`, `artifact`, `error` and `done`; the UI renders them as they arrive.
- **Tools**: any tool you add to the agent shows up in the reply header (`read_file · write_file ›`, click to expand) and in the status line while it runs.
- **Files Argo saves** (ADK artifacts, via `tool_context.save_artifact`) appear as rows under the reply with a VIEW link. They are stored in the GCS bucket named by `GOOGLE_CLOUD_BUCKET` (see below).
- **Attachments**: PDFs, images, audio and video are sent to the model as-is; any other file must be UTF-8 text. 15 MB total per message. Each upload is also saved to the artifact bucket.
- **Artifact storage**: files the agent saves and files you attach are kept in the GCS bucket `GOOGLE_CLOUD_BUCKET` (the API won't start without it). Objects are laid out as `<app>/<user>/<session>/<filename>/<version>`, where `<app>` is `src`, `<user>` is `aseem`, `<session>` is the conversation id (the `?s=` value in the URL) and `<version>` starts at `0`; saving the same filename again in a session adds a new version. Your Google credentials need write access to the bucket.

## API

| Route | |
| --- | --- |
| `POST /api/chat` | `{session_id?, text, attachments[]}` → SSE stream. Omit `session_id` to start a conversation. |
| `GET /api/sessions` | All conversations, newest first. |
| `GET /api/sessions/{id}` | One conversation with its messages. |
| `GET /api/sessions/{id}/artifacts/{name}` | A file the agent saved. |
