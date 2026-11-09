"""HTTP routes and chat handling for the Argo API."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
# The .adk state folder lives in src/, one level above the package.
DATA_DIR = HERE.parent
REPO_ROOT = DATA_DIR.parent

# The Google client reads its project/location settings from the environment,
# so this must happen before the agent is imported. .env is at the repo root
# (see .env.example); in the cluster the settings come from the ConfigMap.
load_dotenv(REPO_ROOT / ".env")

from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402
from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import BaseSessionService, Session  # noqa: E402
from google.adk.sessions.database_session_service import DatabaseSessionService  # noqa: E402
from google.adk.sessions.sqlite_session_service import SqliteSessionService  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from .agent import root_agent  # noqa: E402
from .artifacts import ArtifactStore  # noqa: E402
from .attachments import AttachmentIn, attached_name, process_attachments  # noqa: E402
from .auth import current_user  # noqa: E402
from .tools.sandbox import sandboxes  # noqa: E402

log = logging.getLogger("argo")

# ADK expects the app name to match the directory the agent lives in.
APP_NAME = DATA_DIR.name  # "src": keeps existing sessions in .adk/ visible

STATE_DIR = DATA_DIR / ".adk"  # git-ignored


def make_session_service() -> BaseSessionService:
    """Postgres when DATABASE_URL is set (production), else a local SQLite file.

    DATABASE_URL looks like postgresql+asyncpg://user@host:5432/dbname. On GKE
    it points at the Cloud SQL Auth Proxy sidecar on 127.0.0.1 (see k8s/).
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return DatabaseSessionService(url)
    STATE_DIR.mkdir(exist_ok=True)
    return SqliteSessionService(str(STATE_DIR / "argo.db"))


sessions = make_session_service()

artifacts = ArtifactStore(APP_NAME)

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=sessions,
    artifact_service=artifacts.service,
)

TITLE_MAX = 48

router = APIRouter()


# ── request models ──────────────────────────────────────────────────────────


class ChatIn(BaseModel):
    session_id: str | None = None
    text: str = ""
    attachments: list[AttachmentIn] = Field(default_factory=list)


# ── helpers ─────────────────────────────────────────────────────────────────


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def jsonable(value: Any) -> Any:
    """Tool payloads can hold non-JSON values; stringify those so they survive the wire."""
    return json.loads(json.dumps(value, default=str))


async def tool_output(user: str, session_id: str, name: str, response: Any) -> Any:
    """The tool's response as the UI should show it.

    ADK's load_artifacts returns only a status line and injects the file contents
    straight into the next model request, so they never appear in the response.
    Load them here so the chat can show what the model actually read.
    """
    response = jsonable(response)
    if name != "load_artifacts" or not isinstance(response, dict):
        return response
    contents: dict[str, Any] = {}
    for fname in response.get("artifact_names") or []:
        try:
            part = await artifacts.load(user, session_id, fname)
        except Exception as exc:  # display only; never break the run over it
            contents[fname] = f"(could not load: {exc})"
            continue
        if part is None:
            contents[fname] = "(not found)"
        elif part.text is not None:
            contents[fname] = part.text
        elif part.inline_data:
            data = part.inline_data.data or b""
            mime = part.inline_data.mime_type
            if mime and (mime.startswith("text/") or mime in ("application/json", "image/svg+xml")):
                contents[fname] = data.decode("utf-8", errors="replace")
            else:
                contents[fname] = f"({mime or 'binary'}, {len(data)} bytes)"
    return {**response, "contents": contents}


def parts_of(event: Event) -> list[types.Part]:
    return list(event.content.parts) if event.content and event.content.parts else []


def friendly_error(raw: str | None) -> str:
    """Boil ADK/Vertex error text down to one readable line."""
    lines = (raw or "").strip().splitlines()
    text = lines[-1] if lines else "Something went wrong"
    # e.g. "429 RESOURCE_EXHAUSTED. {'error': {..., 'message': 'Resource exhausted. Please try again later. Please refer to …'}}"
    m = re.match(r"(\d{3} [A-Z_]+)\. \{.*?'message': '(.*?)(?: Please refer to [^']*)?'", text)
    return f"{m[1]}: {m[2]}" if m else text[:300]


def derive_title(text: str, attachments: list[AttachmentIn]) -> str:
    line = " ".join(text.split())
    if not line and attachments:
        line = attachments[0].name
    if len(line) <= TITLE_MAX:
        return line or "New chat"
    cut = line[:TITLE_MAX].rsplit(" ", 1)[0] or line[:TITLE_MAX]
    return cut.rstrip(" ,.;:-") + "…"


def summarize(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "number": session.state.get("number"),
        "title": session.state.get("title") or "New chat",
        "updated_at": session.last_update_time,
    }


def build_user_content(body: ChatIn) -> tuple[types.Content, list[tuple[str, types.Part]]]:
    """The message for the model, plus each upload as (filename, raw-bytes part) to store."""
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Message is empty")

    parts = [types.Part(text=text)] if text else []
    attached, uploads = process_attachments(body.attachments)
    return types.Content(role="user", parts=parts + attached), uploads


async def next_number(user: str) -> int:
    existing = await sessions.list_sessions(app_name=APP_NAME, user_id=user)
    return max((s.state.get("number") or 0 for s in existing.sessions), default=0) + 1


# ── streaming ───────────────────────────────────────────────────────────────


class RunTranslator:
    """Turns ADK events into the small set of SSE events the frontend understands.

    text         {delta}                      reply text, in order
    tool         {id, name, args}             the agent started a tool call
    tool_result  {id, name, response}         a tool call finished, with its output
    artifact     {name, version, lines, mime} the agent saved a file
    error        {message}
    """

    def __init__(self, user: str, session_id: str) -> None:
        self.user = user
        self.session_id = session_id
        # With SSE streaming ADK sends the reply as partial chunks and then once
        # more, whole, in the final event. Only forward the whole if no chunks came.
        self._streamed_text = False

    async def translate(self, event: Event) -> list[tuple[str, dict[str, Any]]]:
        if event.error_code or event.error_message:
            return [("error", {"message": friendly_error(event.error_message or event.error_code)})]

        out: list[tuple[str, dict[str, Any]]] = []
        if event.partial:
            for part in parts_of(event):
                if part.text and not part.thought:
                    self._streamed_text = True
                    out.append(("text", {"delta": part.text}))
            return out

        for part in parts_of(event):
            if part.text and not part.thought and not self._streamed_text:
                out.append(("text", {"delta": part.text}))
            if part.function_call:
                call = part.function_call
                out.append(("tool", {"id": call.id or call.name, "name": call.name, "args": call.args or {}}))
            if part.function_response:
                res = part.function_response
                out.append(("tool_result", {
                    "id": res.id or res.name, "name": res.name,
                    "response": await tool_output(self.user, self.session_id, res.name, res.response),
                }))
        self._streamed_text = False

        for name, version in (event.actions.artifact_delta or {}).items():
            out.append(("artifact", await artifacts.describe(self.user, self.session_id, name, version)))
        return out


async def stream_run(session: Session, content: types.Content) -> AsyncIterator[str]:
    yield sse("session", summarize(session))
    translator = RunTranslator(session.user_id, session.id)
    try:
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=content,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        ):
            for name, data in await translator.translate(event):
                yield sse(name, data)
                if name == "error":
                    return  # one error ends the run; ADK may also raise it afterwards
        yield sse("done", {})
    except Exception as exc:  # the stream has started, so report in-band
        log.exception("run failed for session %s", session.id)
        yield sse("error", {"message": friendly_error(str(exc)) or type(exc).__name__})


# ── history ─────────────────────────────────────────────────────────────────


async def history(session: Session) -> list[dict[str, Any]]:
    """Rebuild the chat transcript from stored events.

    A user event starts a user message; every agent event after it (text, tool
    calls, tool results, artifacts) is folded into one Argo message, exactly
    like the live stream does.
    """
    messages: list[dict[str, Any]] = []
    argo: dict[str, Any] | None = None

    for event in session.events:
        parts = parts_of(event)
        if event.author == "user":
            argo = None
            texts, files = [], []
            for part in parts:
                if not part.text:
                    continue
                if (name := attached_name(part.text)) is not None:
                    files.append({"name": name})
                else:
                    texts.append(part.text)
            messages.append({
                "id": event.id, "role": "user",
                "text": "\n".join(texts), "attachments": files,
            })
            continue

        if argo is None:
            argo = {"id": event.id, "role": "argo", "text": "", "tools": [], "artifacts": [], "error": None}
            messages.append(argo)
        if event.error_code or event.error_message:
            argo["error"] = friendly_error(event.error_message or event.error_code)
        for part in parts:
            if part.text and not part.thought:
                argo["text"] += ("\n\n" if argo["text"] else "") + part.text
            if part.function_call:
                call = part.function_call
                argo["tools"].append({
                    "id": call.id or call.name, "name": call.name,
                    "args": jsonable(call.args or {}), "response": None,
                })
            if part.function_response:
                res = part.function_response
                rid = res.id or res.name
                for tool in reversed(argo["tools"]):
                    if tool["id"] == rid:
                        tool["response"] = await tool_output(session.user_id, session.id, res.name, res.response)
                        break
        for name, version in (event.actions.artifact_delta or {}).items():
            argo["artifacts"].append(await artifacts.describe(session.user_id, session.id, name, version))

    # Drop shells left by events with nothing to show (e.g. thought signatures only).
    return [
        m for m in messages
        if m["role"] == "user" or m["text"] or m["tools"] or m["artifacts"] or m["error"]
    ]


# ── routes ──────────────────────────────────────────────────────────────────


@router.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness/readiness check for Kubernetes and the load balancer."""
    return {"status": "ok"}


@router.get("/api/sessions")
async def list_sessions(user: str = Depends(current_user)) -> list[dict[str, Any]]:
    found = await sessions.list_sessions(app_name=APP_NAME, user_id=user)
    return sorted((summarize(s) for s in found.sessions), key=lambda s: s["updated_at"], reverse=True)


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    session = await sessions.get_session(app_name=APP_NAME, user_id=user, session_id=session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return {**summarize(session), "messages": await history(session)}


@router.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, user: str = Depends(current_user)) -> Response:
    await sandboxes.release(APP_NAME, user, session_id)
    await sessions.delete_session(app_name=APP_NAME, user_id=user, session_id=session_id)
    return Response(status_code=204)


@router.post("/api/chat")
async def chat(body: ChatIn, user: str = Depends(current_user)) -> StreamingResponse:
    content, uploads = build_user_content(body)
    if body.session_id:
        session = await sessions.get_session(
            app_name=APP_NAME, user_id=user, session_id=body.session_id
        )
        if session is None:
            raise HTTPException(404, "Session not found")
    else:
        session = await sessions.create_session(
            app_name=APP_NAME,
            user_id=user,
            state={"number": await next_number(user), "title": derive_title(body.text, body.attachments)},
        )
    for name, part in uploads:
        await artifacts.save(user, session.id, name, part)
    return StreamingResponse(
        stream_run(session, content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Shown inline by the browser; anything else (html, svg, …) is forced to
# download so agent-written files can't run scripts on this origin.
INLINE_SAFE = re.compile(r"^(image/(png|jpeg|gif|webp)|application/pdf)$")


@router.get("/api/sessions/{session_id}/artifacts/{name:path}")
async def get_artifact(
    session_id: str, name: str, version: int | None = None, user: str = Depends(current_user)
) -> Response:
    try:
        part = await artifacts.load(user, session_id, name, version)
    except ValueError:
        raise HTTPException(400, "Invalid artifact name") from None
    if part is None:
        raise HTTPException(404, "Artifact not found")

    headers = {"X-Content-Type-Options": "nosniff"}
    if part.inline_data and part.inline_data.data is not None:
        mime = part.inline_data.mime_type or "application/octet-stream"
        if not INLINE_SAFE.match(mime):
            headers["Content-Disposition"] = f'attachment; filename="{Path(name).name}"'
        return Response(part.inline_data.data, media_type=mime, headers=headers)
    return Response(part.text or "", media_type="text/plain; charset=utf-8", headers=headers)

