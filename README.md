# Argo

Argo is a personal AI agent: a Google ADK agent (`src/argo/agent.py`) with a chat UI. Each signed-in person has their own conversations and files.

```
src/argo/   the agent (agent.py), API routes (routes.py), sign-in (auth.py), and entry point (main.py)
frontend/   the chat UI: Vite + React + TypeScript
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python 3.15 (see `.python-version`; uv fetches it if needed)
- Node.js 20.19+ or 22.12+, and npm
- Docker Desktop, kind (0.31.0+) and kubectl for the local sandbox cluster.
- Google Cloud access for Vertex AI. Sign in once with `gcloud auth application-default login`.
- `.env` at the repo root (git-ignored): `cp .env.example .env` and fill in `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_BUCKET`. That, plus the local sandbox cluster below, is all a local run needs; the rest of the file is used by the production scripts. Conversations go to a local SQLite file unless you set `DATABASE_URL`.

## Set up (first time)

From the repo root:

```sh
uv sync                        # Python dependencies into .venv
scripts/local-sandbox.sh up     # kind cluster, controller, runtime image
cd frontend && npm install     # frontend dependencies
```

## Start the app (development)

Keep the sandbox router tunnel running in its own terminal:

```sh
scripts/local-sandbox.sh tunnel
```

The API won't start without the `kind-argo-dev` cluster from `up`, and without
the tunnel the sandbox tools return errors.

Then start the API and UI in two more terminals, both from the repo root.

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

## Build the app image

The [Dockerfile](Dockerfile) builds the chat UI and packages it with the API in
one non-root image. On GKE it uses in-cluster Kubernetes credentials and direct
sandbox pod connections. Credentials are not baked into
either image.

Build from the repo root:

```sh
docker build -t argo .
```

For local development, run the API on the Mac as above. It uses only the
`kind-argo-dev` kubectl context, never your current one, and refuses to start if
that context's API server isn't local. A separate Docker container has its own
loopback interface, so it can't reach the cluster or the tunnel.
The sandbox image itself is built and loaded into kind by `local-sandbox.sh up`.

## Production

Argo is live at https://muhammadaseem.com/argo/, on GKE behind a Google login (IAP), with conversations in Cloud SQL (Postgres). Only the accounts in `IAP_MEMBERS` can sign in, and each sees only their own chats and files. To ship a change:

```sh
uvx bump2version patch     # or minor / major; bumps the version, commits, tags
git push --follow-tags     # CI builds and pushes the image
scripts/deploy.sh          # roll it out
```

How it's put together, what's running where, and what to check when something breaks: [docs/production.md](docs/production.md).

## How it fits together

- **Users**: in production (`ARGO_AUTH=iap`, set in [k8s/configmap.yaml](k8s/configmap.yaml)) the API verifies the signed JWT that IAP adds to every request and uses the signed-in Google email as the user id; requests without a valid token get `401`. Locally there is no sign-in, and every request is `LOCAL_USER_ID` (default `aseem`). See [src/argo/auth.py](src/argo/auth.py).
- **Conversations** belong to the user who started them and are stored in Postgres when `DATABASE_URL` is set (production), otherwise in `src/.adk/argo.db` (git-ignored). The open one is in the URL as `?s=<id>`, so a reload keeps it. The New chat button in the sidebar starts a new chat.
- **Replies stream** over server-sent events from `POST /argo/api/chat`. The API turns ADK events into `text`, `tool`, `tool_result`, `artifact`, `error` and `done`; the UI renders them as they arrive.
- **Tools**: any tool you add to the agent shows up in the reply header (`read_file · write_file ›`, click to expand) and in the status line while it runs.
- **Sandbox tools**: ADK's `Execute`, `ReadFile`, `EditFile` and `WriteFile`, plus skill scripts, share one sandbox per conversation (gVisor on GKE). `stage_artifact` copies attachments into `/workspace/inputs/`; `save_output` turns a sandbox file into a versioned, downloadable artifact. Workspaces expire after 30 idle minutes; saved artifacts persist. See [sandbox architecture and verification](docs/production.md#agent-sandbox-architecture-and-implementation-status).
- **Files Argo saves** (ADK artifacts, via `tool_context.save_artifact`) appear as rows under the reply with a VIEW link. They are stored in the GCS bucket named by `GOOGLE_CLOUD_BUCKET` (see below).
- **Artifact tools**: Google's built-in `load_artifacts` loads saved file contents into the model's next request. Thin wrappers in `src/argo/tools/artifacts.py` expose saving text or base64-encoded binary files, listing filenames, inspecting version metadata, listing versions, and deleting a file with all its versions. Every call uses the Runner's ADK artifact service and current user/session; a `user:` filename prefix makes a file available across that user's chats. Saving a filename again creates a new version.
- **Attachments**: PDFs, images, audio and video are sent to the model as-is; other UTF-8 files are included as text. Binary files are saved as artifacts and can be processed with `stage_artifact`. 15 MB total per message. Every upload is saved to the artifact bucket.
- **Artifact storage**: files the agent saves and files you attach are kept in the GCS bucket `GOOGLE_CLOUD_BUCKET` (the API won't start without it). Objects are laid out as `<app>/<user>/<session>/<filename>/<version>`, where `<app>` is `src`, `<user>` is the signed-in Google email in production (`aseem` locally, see `LOCAL_USER_ID`), `<session>` is the conversation id (the `?s=` value in the URL) and `<version>` starts at `0`; saving the same filename again in a session adds a new version. Your Google credentials need write access to the bucket.

## API

| Route | |
| --- | --- |
| `POST /argo/api/chat` | `{session_id?, text, attachments[]}` → SSE stream. Omit `session_id` to start a conversation. |
| `GET /argo/api/sessions` | The current user's conversations, newest first. |
| `GET /argo/api/sessions/{id}` | One conversation with its messages. |
| `DELETE /argo/api/sessions/{id}` | Delete a conversation. |
| `GET /argo/api/sessions/{id}/artifacts/{name}` | A file the agent saved. |
| `GET /argo/api/health` | Health check (used by Kubernetes). |

Every route except `health` acts as the current user (see **Users** above), so a conversation or file that belongs to someone else returns `404`.
