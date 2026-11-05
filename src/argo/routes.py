"""HTTP routes and chat handling for the Argo API."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
# .env and the .adk state folder live in src/, one level above the package.
DATA_DIR = HERE.parent

# The Google client reads its project/location settings from the environment,
# so this must happen before the agent is imported.
load_dotenv(DATA_DIR / ".env")

from fastapi import APIRouter, HTTPException  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402
from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: E402
from google.adk.artifacts import FileArtifactService  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import Session  # noqa: E402
from google.adk.sessions.sqlite_session_service import SqliteSessionService  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from .agent import root_agent  # noqa: E402

log = logging.getLogger("argo")

# ADK expects the app name to match the directory the agent lives in.
APP_NAME = DATA_DIR.name  # "src": keeps existing sessions in .adk/ visible
USER_ID = "aseem"

STATE_DIR = DATA_DIR / ".adk"  # git-ignored
STATE_DIR.mkdir(exist_ok=True)

sessions = SqliteSessionService(str(STATE_DIR / "argo.db"))
artifacts = FileArtifactService(root_dir=str(STATE_DIR / "artifacts"))
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=sessions,
    artifact_service=artifacts,
)

# Gemini accepts these directly; any other upload must be readable as UTF-8
# text, which is then sent inline as a text part.
INLINE_MIME_PREFIXES = ("image/", "audio/", "video/")
INLINE_MIME_TYPES = {"application/pdf"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # Vertex caps a request at ~20 MB
ATTACHED_PREFIX = "Attached file: "  # marks attachment parts so history can rebuild them
TITLE_MAX = 48

router = APIRouter()


# ── request models ──────────────────────────────────────────────────────────


class AttachmentIn(BaseModel):
    name: str
    mime_type: str = ""
    data: str  # base64


class ChatIn(BaseModel):
    session_id: str | None = None
    text: str = ""
    attachments: list[AttachmentIn] = Field(default_factory=list)


# ── helpers ─────────────────────────────────────────────────────────────────


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


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


def build_user_content(body: ChatIn) -> types.Content:
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Message is empty")

    parts: list[types.Part] = [types.Part(text=text)] if text else []
    total = 0
    for att in body.attachments:
        try:
            raw = base64.b64decode(att.data, validate=True)
        except ValueError:
            raise HTTPException(400, f"{att.name}: not valid base64") from None
        total += len(raw)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Attachments are too large (15 MB max in total)")

        name = Path(att.name.replace("\n", " ")).name[:200] or "file"
        mime = att.mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if mime in INLINE_MIME_TYPES or mime.startswith(INLINE_MIME_PREFIXES):
            parts.append(types.Part(text=f"{ATTACHED_PREFIX}{name}"))
            parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=raw)))
            continue
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, f"{name}: unsupported file type") from None
        parts.append(types.Part(text=f"{ATTACHED_PREFIX}{name}\n\n{body_text}"))
    return types.Content(role="user", parts=parts)


async def next_number() -> int:
    existing = await sessions.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    return max((s.state.get("number") or 0 for s in existing.sessions), default=0) + 1


async def describe_artifact(session_id: str, name: str, version: int) -> dict[str, Any]:
    """Artifact metadata for the chat row: name plus a line count for text files."""
    info: dict[str, Any] = {"name": name, "version": version, "lines": None, "mime": None}
    part = await artifacts.load_artifact(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id,
        filename=name, version=version,
    )
    if part is None:
        return info
    if part.inline_data:
        info["mime"] = part.inline_data.mime_type
    elif part.text is not None:
        info["mime"] = "text/plain"
        info["lines"] = len(part.text.splitlines())
    return info


# ── streaming ───────────────────────────────────────────────────────────────


class RunTranslator:
    """Turns ADK events into the small set of SSE events the frontend understands.

    text         {delta}                      reply text, in order
    tool         {id, name, args}             the agent started a tool call
    tool_result  {id, name}                   a tool call finished
    artifact     {name, version, lines, mime} the agent saved a file
    error        {message}
    """

    def __init__(self, session_id: str) -> None:
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
                out.append(("tool_result", {"id": res.id or res.name, "name": res.name}))
        self._streamed_text = False

        for name, version in (event.actions.artifact_delta or {}).items():
            out.append(("artifact", await describe_artifact(self.session_id, name, version)))
        return out


async def stream_run(session: Session, content: types.Content) -> AsyncIterator[str]:
    yield sse("session", summarize(session))
    translator = RunTranslator(session.id)
    try:
        async for event in runner.run_async(
            user_id=USER_ID,
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
                if part.text.startswith(ATTACHED_PREFIX):
                    files.append({"name": part.text[len(ATTACHED_PREFIX):].split("\n", 1)[0]})
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
                argo["tools"].append({"id": call.id or call.name, "name": call.name, "args": call.args or {}})
        for name, version in (event.actions.artifact_delta or {}).items():
            argo["artifacts"].append(await describe_artifact(session.id, name, version))

    # Drop shells left by events with nothing to show (e.g. thought signatures only).
    return [
        m for m in messages
        if m["role"] == "user" or m["text"] or m["tools"] or m["artifacts"] or m["error"]
    ]


# ── routes ──────────────────────────────────────────────────────────────────


@router.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    found = await sessions.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    return sorted((summarize(s) for s in found.sessions), key=lambda s: s["updated_at"], reverse=True)


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    session = await sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return {**summarize(session), "messages": await history(session)}


@router.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    content = build_user_content(body)
    if body.session_id:
        session = await sessions.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=body.session_id
        )
        if session is None:
            raise HTTPException(404, "Session not found")
    else:
        session = await sessions.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            state={"number": await next_number(), "title": derive_title(body.text, body.attachments)},
        )
    return StreamingResponse(
        stream_run(session, content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Shown inline by the browser; anything else (html, svg, …) is forced to
# download so agent-written files can't run scripts on this origin.
INLINE_SAFE = re.compile(r"^(image/(png|jpeg|gif|webp)|application/pdf)$")


@router.get("/api/sessions/{session_id}/artifacts/{name:path}")
async def get_artifact(session_id: str, name: str, version: int | None = None) -> Response:
    try:
        part = await artifacts.load_artifact(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id,
            filename=name, version=version,
        )
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

