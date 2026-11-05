"""Artifact storage: every file the agent saves or the user attaches lives in GCS.

Objects are laid out by ADK as ``<app>/<user>/<session>/<filename>/<version>``;
see the README for details.
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.artifacts import GcsArtifactService
from google.genai import types

BUCKET_ENV = "GOOGLE_CLOUD_BUCKET"


class ArtifactStore:
    """The app's artifact bucket, scoped to one app and user."""

    def __init__(self, app_name: str, user_id: str) -> None:
        # Accept a bare name or a gs://name/ path.
        bucket = os.environ.get(BUCKET_ENV, "").strip().removeprefix("gs://").strip("/")
        if not bucket:
            raise RuntimeError(f"{BUCKET_ENV} must be set (in src/.env) to a GCS bucket for artifacts")
        self.app_name = app_name
        self.user_id = user_id
        # Handed to the ADK Runner, which saves the agent's artifacts through it.
        self.service = GcsArtifactService(bucket_name=bucket)

    async def save(self, session_id: str, name: str, part: types.Part) -> int:
        """Save a new version of `name`; returns its version number."""
        return await self.service.save_artifact(
            app_name=self.app_name, user_id=self.user_id, session_id=session_id,
            filename=name, artifact=part,
        )

    async def load(self, session_id: str, name: str, version: int | None = None) -> types.Part | None:
        """The given version of `name`, or the latest when `version` is None."""
        return await self.service.load_artifact(
            app_name=self.app_name, user_id=self.user_id, session_id=session_id,
            filename=name, version=version,
        )

    async def describe(self, session_id: str, name: str, version: int) -> dict[str, Any]:
        """Metadata for the chat row: name plus a line count for text files."""
        info: dict[str, Any] = {"name": name, "version": version, "lines": None, "mime": None}
        part = await self.load(session_id, name, version)
        if part is None:
            return info
        if part.inline_data:
            info["mime"] = part.inline_data.mime_type
        elif part.text is not None:
            info["mime"] = "text/plain"
            info["lines"] = len(part.text.splitlines())
        return info
