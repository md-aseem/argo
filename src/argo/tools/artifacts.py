"""Agent tools backed by the Runner's ADK artifact service."""

from __future__ import annotations

import base64
from typing import Any

from google.adk.artifacts.base_artifact_service import BaseArtifactService
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types


def _service_and_scope(tool_context: ToolContext) -> tuple[BaseArtifactService, dict[str, str]]:
    # ADK exposes deletion and version listing only on the service. Keep access
    # to its invocation context here; the model never supplies scope identifiers.
    invocation = tool_context._invocation_context
    service = invocation.artifact_service
    if service is None:
        raise ValueError("Artifact service is not initialized.")
    return service, {
        "app_name": invocation.app_name,
        "user_id": invocation.user_id,
        "session_id": invocation.session.id,
    }


async def save_artifact(
    filename: str,
    content: str,
    tool_context: ToolContext,
    mime_type: str = "text/plain",
    base64_encoded: bool = False,
    custom_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save a file, creating a new version if the filename already exists.

    Args:
        filename: Filename including extension. Use user: only for files that
            should be available across this user's conversations.
        content: The complete file contents, as text or base64-encoded bytes.
        mime_type: The file's MIME type, such as text/plain or application/pdf.
        base64_encoded: Set true only when content contains base64-encoded bytes.
        custom_metadata: Optional metadata to store alongside this version.
            The GCS backend stores metadata values as strings.
    """
    if base64_encoded:
        try:
            data = base64.b64decode(content, validate=True)
        except ValueError:
            return {"error": "content must be valid base64 when base64_encoded is true"}
        part = types.Part.from_bytes(data=data, mime_type=mime_type)
    elif mime_type == "text/plain":
        part = types.Part.from_text(text=content)
    else:
        part = types.Part.from_bytes(data=content.encode("utf-8"), mime_type=mime_type)

    version = await tool_context.save_artifact(
        filename=filename, artifact=part, custom_metadata=custom_metadata,
    )
    return {"filename": filename, "version": version}


async def list_artifacts(tool_context: ToolContext) -> dict[str, Any]:
    """List filenames available in this conversation, including user-scoped files."""
    return {"filenames": await tool_context.list_artifacts()}


async def get_artifact_version(
    filename: str,
    tool_context: ToolContext,
    version: int | None = None,
) -> dict[str, Any]:
    """Get an artifact's metadata without loading its contents.

    Args:
        filename: The artifact filename, retaining any user: prefix.
        version: Version number, or omit to get the latest version's metadata.
    """
    info = await tool_context.get_artifact_version(filename=filename, version=version)
    if info is None:
        return {"error": "Artifact or version not found", "filename": filename, "version": version}
    return {"filename": filename, **info.model_dump(mode="json")}


async def delete_artifact(filename: str, tool_context: ToolContext) -> dict[str, Any]:
    """Delete an artifact and ALL its versions. Does nothing if it is missing.

    Args:
        filename: The artifact filename, retaining any user: prefix.
    """
    service, scope = _service_and_scope(tool_context)
    await service.delete_artifact(**scope, filename=filename)
    return {"filename": filename, "status": "deleted"}


async def list_versions(filename: str, tool_context: ToolContext) -> dict[str, Any]:
    """List the available version numbers for an artifact.

    Args:
        filename: The artifact filename, retaining any user: prefix.
    """
    service, scope = _service_and_scope(tool_context)
    return {"filename": filename, "versions": await service.list_versions(**scope, filename=filename)}


async def list_artifact_versions(filename: str, tool_context: ToolContext) -> dict[str, Any]:
    """List metadata for every version of an artifact without loading contents.

    Args:
        filename: The artifact filename, retaining any user: prefix.
    """
    service, scope = _service_and_scope(tool_context)
    versions = await service.list_artifact_versions(**scope, filename=filename)
    return {"filename": filename, "versions": [info.model_dump(mode="json") for info in versions]}


artifact_tools = [
    LoadArtifactsTool(),
    save_artifact,
    list_artifacts,
    get_artifact_version,
    delete_artifact,
    list_versions,
    list_artifact_versions,
]
