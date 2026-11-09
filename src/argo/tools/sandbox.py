"""Per-conversation Agent Sandbox workspaces behind ADK's environment and skill tools.

One SandboxEnvironment serves every conversation. ADK's environment interface
doesn't say which conversation a call is for, so the agent's before_tool_callback
(bind_sandbox) records the call's ToolContext in a ContextVar. ADK awaits that
callback in the same coroutine as the tool, and runs parallel tool calls as
separate tasks, each with its own copy.

Each conversation owns one SandboxClaim with a deterministic name, so restarts
reattach to the same workspace. The controller deletes a claim once it has gone
IDLE_SECONDS without a tool call.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import mimetypes
import os
import shlex
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from google.adk.environment import BaseEnvironment, ExecutionResult
from google.adk.skills import load_skills_from_dir
from google.adk.tools import ToolContext
from google.adk.tools.environment import EnvironmentToolset
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset
from google.genai import types
from k8s_agent_sandbox import AsyncSandboxClient
from k8s_agent_sandbox.async_sandbox import AsyncSandbox
from k8s_agent_sandbox.exceptions import SandboxRequestError
from k8s_agent_sandbox.models import (
    SandboxDirectConnectionConfig,
    SandboxFileConfig,
    SandboxInClusterConnectionConfig,
)
from k8s_agent_sandbox.utils import construct_sandbox_claim_lifecycle_spec
from kubernetes_asyncio import client as k8s
from kubernetes_asyncio import config as k8s_config

# Locally, always the kind cluster by name: kubectl's current context may be production.
LOCAL_CONTEXT = "kind-argo-dev"
LOCAL_ROUTER_URL = "http://127.0.0.1:18080"  # scripts/local-sandbox.sh tunnel
NAMESPACE = "argo-sandbox"
WARMPOOL = "argo-python"
IDLE_SECONDS = 30 * 60
COMMAND_SECONDS = 60  # The runtime's own limit (SANDBOX_EXEC_TIMEOUT_SECONDS).
FILE_BYTES = 15 * 1024 * 1024
UID_STATE = "argo_sandbox_uid"
RESET_NOTICE = (
    "This conversation's previous sandbox expired or was deleted, so its unsaved "
    "files are gone. Stage any saved artifacts you need again."
)


def claim_name(app_name: str, user_id: str, session_id: str) -> str:
    key = json.dumps([app_name, user_id, session_id], separators=(",", ":"))
    return "argo-" + hashlib.sha256(key.encode()).hexdigest()[:20]


def workspace_path(path: str | PurePosixPath) -> str:
    """Map a path to the workspace-relative form the SDK expects.

    The SDK and runtime strip a leading "/", so /workspace/a.py must become a.py
    or it would land in /workspace/workspace/a.py.
    """
    p = PurePosixPath(str(path))
    if p.is_absolute():
        try:
            p = p.relative_to("/workspace")
        except ValueError:
            raise ValueError(f"{path} is outside /workspace") from None
    if not p.parts or ".." in p.parts:
        raise ValueError(f"{path!r} is not a file path inside /workspace")
    return str(p)


def _is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class _Call:
    """One tool call. Mutable, so the tasks SkillToolset spawns share it."""

    context: ToolContext
    sandbox: AsyncSandbox | None = None
    reset: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_call: ContextVar[_Call] = ContextVar("argo_sandbox_call")


async def bind_sandbox(tool, args, tool_context):
    """Agent before_tool_callback: route this call's sandbox operations."""
    _call.set(_Call(tool_context))


async def report_reset(tool, args, tool_context, tool_response):
    """Agent after_tool_callback: tell the model when its workspace was replaced."""
    call = _call.get(None)
    if call and call.context is tool_context and call.reset:
        return {**tool_response, "workspace_reset": RESET_NOTICE}
    return None


class SandboxEnvironment(BaseEnvironment):
    """The calling conversation's sandbox, resolved per tool call."""

    def __init__(self) -> None:
        self.client: AsyncSandboxClient | None = None
        self.claims: k8s.CustomObjectsApi | None = None

    @property
    def working_dir(self) -> Path:
        return Path("/workspace")

    async def initialize(self) -> None:
        """Load credentials: in-cluster on GKE, the named kind context locally."""
        if self.client is None:
            configuration = k8s.Configuration()
            if os.getenv("KUBERNETES_SERVICE_HOST"):
                k8s_config.load_incluster_config(client_configuration=configuration)
                connection = SandboxInClusterConnectionConfig()
            else:
                try:
                    await k8s_config.load_kube_config(
                        context=LOCAL_CONTEXT, client_configuration=configuration,
                    )
                except k8s_config.ConfigException as exc:
                    raise RuntimeError(
                        f"No {LOCAL_CONTEXT} context; run scripts/local-sandbox.sh up"
                    ) from exc
                if not _is_loopback(configuration.host):
                    raise RuntimeError(
                        f"{LOCAL_CONTEXT} must use a local API server, not {configuration.host}"
                    )
                connection = SandboxDirectConnectionConfig(api_url=LOCAL_ROUTER_URL)
            api = k8s.ApiClient(configuration)
            self.claims = k8s.CustomObjectsApi(api)
            self.client = AsyncSandboxClient(
                api_client=api, connection_config=connection, cleanup=False,
                file_config=SandboxFileConfig(max_download_size=FILE_BYTES),
            )
        self.is_initialized = True

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            await self.claims.api_client.close()  # The SDK leaves an injected client open.
            self.client = self.claims = None
        self.is_initialized = False

    async def _sandbox(self) -> AsyncSandbox:
        call = _call.get(None)
        if call is None:
            raise RuntimeError("The sandbox is only available inside a tool call")
        async with call.lock:
            if call.sandbox is None:
                call.sandbox = await self._acquire(call)
        return call.sandbox

    async def _acquire(self, call: _Call) -> AsyncSandbox:
        session = call.context.session
        name = claim_name(session.app_name, session.user_id, session.id)
        sandbox = await self.client.create_sandbox(
            WARMPOOL, NAMESPACE, sandbox_ready_timeout=120,
            labels={"app.kubernetes.io/managed-by": "argo"},
            claim_name=name, adopt_existing=True, shutdown_after_seconds=IDLE_SECONDS,
        )
        # Adopting a claim keeps its old deadline, so push it out on every tool
        # call. A call's commands each stop within a minute, far inside it.
        claim = await self.claims.patch_namespaced_custom_object(
            "extensions.agents.x-k8s.io", "v1beta1", NAMESPACE, "sandboxclaims", name,
            {"spec": {"lifecycle": construct_sandbox_claim_lifecycle_spec(IDLE_SECONDS)}},
            _content_type="application/merge-patch+json", _request_timeout=15,
        )
        uid = claim["metadata"]["uid"]
        state = call.context.state
        call.reset = state.get(UID_STATE) not in (None, uid)
        state[UID_STATE] = uid
        return sandbox

    async def execute(self, command: str, *, timeout: float | None = None) -> ExecutionResult:
        sandbox = await self._sandbox()
        seconds = min(timeout or COMMAND_SECONDS, COMMAND_SECONDS)
        # The runtime runs /bin/sh (dash); models write Bash. Output isn't capped:
        # the runtime returns all of it, and ADK truncates what the model sees.
        result = await sandbox.commands.run(
            shlex.join(["bash", "-c", command]),
            timeout=COMMAND_SECONDS + 15, command_timeout=seconds,
        )
        return ExecutionResult(
            exit_code=result.exit_code, stdout=result.stdout,
            stderr=result.stderr, timed_out=result.timed_out,
        )

    async def read_file(self, path: Path) -> bytes:
        relative = workspace_path(path)
        sandbox = await self._sandbox()
        try:
            return await sandbox.files.read(relative, timeout=30)
        except SandboxRequestError as exc:
            if exc.status_code == 404:  # SkillToolset rewrites a skill's files on this.
                raise FileNotFoundError(path) from exc
            raise

    async def write_file(self, path: Path, content: str | bytes) -> None:
        relative = workspace_path(path)
        data = content.encode() if isinstance(content, str) else content
        await (await self._sandbox()).files.write(relative, data, timeout=30)

    async def release(self, app_name: str, user_id: str, session_id: str) -> None:
        """Delete a conversation's sandbox and its unsaved files, if it has one."""
        await self.client.delete_sandbox(claim_name(app_name, user_id, session_id), NAMESPACE)


sandboxes = SandboxEnvironment()


async def stage_artifact(filename: str, tool_context: ToolContext) -> dict[str, Any]:
    """Copy a saved artifact or attachment, including binary files, into /workspace/inputs/.

    The sandbox has no network access, so this is how files get into it.

    Args:
        filename: Exact artifact filename, including user: when applicable.
    """
    part = await tool_context.load_artifact(filename)
    if part is None:
        return {"status": "error", "error": f"No artifact named {filename!r}"}
    if part.inline_data and part.inline_data.data is not None:
        data = part.inline_data.data
    elif part.text is not None:
        data = part.text.encode()
    else:
        return {"status": "error", "error": f"{filename!r} has no file content"}
    path = "inputs/" + workspace_path(filename.removeprefix("user:"))
    await sandboxes.write_file(PurePosixPath(path), data)
    return {"status": "ok", "path": "/workspace/" + path, "bytes": len(data)}


async def save_output(path: str, tool_context: ToolContext) -> dict[str, Any]:
    """Save a sandbox file for the user as a downloadable, versioned artifact.

    Use it for anything the user should keep, such as a chart, spreadsheet or
    report. The sandbox is temporary: it is deleted after 30 idle minutes. The
    artifact is named after the file.

    Args:
        path: The file's path, relative to /workspace or under it, such as output/chart.png.
    """
    data = await sandboxes.read_file(PurePosixPath(path))
    filename = PurePosixPath(path).name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    version = await tool_context.save_artifact(
        filename=filename, artifact=types.Part.from_bytes(data=data, mime_type=mime_type),
    )
    return {"status": "ok", "filename": filename, "version": version}


# Both toolsets share one environment that follows the calling conversation, so
# the agent must also register bind_sandbox (and report_reset) as tool callbacks.
sandbox_tools = [
    EnvironmentToolset(environment=sandboxes, max_output_chars=16000),
    SkillToolset(
        skills=load_skills_from_dir(Path(__file__).parents[1] / "skills"),
        discovery_mode=SkillDiscoveryMode.EAGER,
        environment=sandboxes,
        script_timeout=COMMAND_SECONDS,
    ),
    stage_artifact,
    save_output,
]
