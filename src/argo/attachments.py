"""Turning files the user attaches into parts of the message sent to the model.

Storing them is the artifact store's job (see artifacts.py).
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from google.genai import types
from pydantic import BaseModel

# Gemini accepts these directly. Other uploads are sent as UTF-8 text when
# readable, or referenced by filename while their bytes stay in artifacts.
INLINE_MIME_PREFIXES = ("image/", "audio/", "video/")
INLINE_MIME_TYPES = {"application/pdf"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # Vertex caps a request at ~20 MB
ATTACHED_PREFIX = "Attached file: "  # marks attachment parts so history can rebuild them


class AttachmentIn(BaseModel):
    name: str
    mime_type: str = ""
    data: str  # base64


def attached_name(text: str) -> str | None:
    """The filename if `text` is an attachment marker part, else None."""
    if not text.startswith(ATTACHED_PREFIX):
        return None
    return text[len(ATTACHED_PREFIX):].split("\n", 1)[0]


def process_attachments(
    attachments: list[AttachmentIn],
) -> tuple[list[types.Part], list[tuple[str, types.Part]]]:
    """Message parts for the model, plus each upload as (filename, raw-bytes part) to store."""
    parts: list[types.Part] = []
    uploads: list[tuple[str, types.Part]] = []
    total = 0
    for att in attachments:
        try:
            raw = base64.b64decode(att.data, validate=True)
        except ValueError:
            raise HTTPException(400, f"{att.name}: not valid base64") from None
        total += len(raw)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Attachments are too large (15 MB max in total)")

        name = Path(att.name.replace("\n", " ")).name[:200] or "file"
        mime = att.mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        uploads.append((name, types.Part(inline_data=types.Blob(mime_type=mime, data=raw))))
        if mime in INLINE_MIME_TYPES or mime.startswith(INLINE_MIME_PREFIXES):
            parts.append(types.Part(text=f"{ATTACHED_PREFIX}{name}"))
            parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=raw)))
            continue
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            parts.append(types.Part(text=f"{ATTACHED_PREFIX}{name}"))
        else:
            parts.append(types.Part(text=f"{ATTACHED_PREFIX}{name}\n\n{body_text}"))
    return parts, uploads
