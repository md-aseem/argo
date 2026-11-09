"""Sandbox routing and tools: real ADK Runner, tools and contexts; a fake cluster."""

import asyncio
import contextvars
import itertools
import os
import shlex
import unittest
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.skills.models import Frontmatter, Resources, Script, Skill
from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types
from k8s_agent_sandbox.exceptions import SandboxRequestError

from argo.agent import root_agent
from argo.tools import sandbox as sb

_uids = itertools.count()


class FakeSandbox:
    """One claim's workspace, behind the SDK's commands and files APIs."""

    def __init__(self):
        self.uid = f"uid-{next(_uids)}"
        self.data = {}
        self.runs = []
        self.commands = SimpleNamespace(run=self.run)
        self.files = SimpleNamespace(read=self.read, write=self.write)

    async def run(self, command, timeout=60, command_timeout=None):
        await asyncio.sleep(0)
        self.runs.append((shlex.split(command), command_timeout))
        return SimpleNamespace(exit_code=0, stdout="ok\n", stderr="", timed_out=False)

    async def read(self, path, timeout=60):
        await asyncio.sleep(0)
        if path not in self.data:
            raise SandboxRequestError("not found", status_code=404)
        return self.data[path]

    async def write(self, path, data, timeout=60):
        await asyncio.sleep(0)
        self.data[path] = data


class FakeCluster:
    """Stands in for the SDK client and the claims API: one sandbox per claim name."""

    def __init__(self):
        self.sandboxes = {}
        self.deleted = []

    async def create_sandbox(self, warmpool, namespace, **kwargs):
        await asyncio.sleep(0)
        assert kwargs["adopt_existing"] and "env" not in kwargs
        if kwargs["claim_name"] not in self.sandboxes:
            self.sandboxes[kwargs["claim_name"]] = FakeSandbox()
        return self.sandboxes[kwargs["claim_name"]]

    async def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body, **kw):
        assert body["spec"]["lifecycle"]["shutdownPolicy"] == "Delete"
        return {"metadata": {"uid": self.sandboxes[name].uid}}

    async def delete_sandbox(self, name, namespace):
        self.deleted.append(name)
        self.sandboxes.pop(name, None)

    def workspace(self, user, session="chat-1"):
        return self.sandboxes[sb.claim_name("argo", user, session)]


class ScriptedModel(BaseLlm):
    responses: list

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=self.responses.pop(0)))


def calls(*specs):
    return [types.Part.from_function_call(name=name, args=args) for name, args in specs]


DONE = [types.Part.from_text(text="done")]


class SandboxToolsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cluster = FakeCluster()
        sb.sandboxes.client = sb.sandboxes.claims = self.cluster
        self.sessions = InMemorySessionService()
        self.artifacts = InMemoryArtifactService()

    async def asyncTearDown(self):
        sb.sandboxes.client = sb.sandboxes.claims = None

    async def turn(self, model, user):
        """One chat turn through ADK's Runner; returns tool responses by tool name."""
        if not await self.sessions.get_session(app_name="argo", user_id=user, session_id="chat-1"):
            await self.sessions.create_session(app_name="argo", user_id=user, session_id="chat-1")
        runner = Runner(
            agent=root_agent.clone(update={"model": model}), app_name="argo",
            session_service=self.sessions, artifact_service=self.artifacts,
        )
        responses = {}
        message = types.Content(role="user", parts=[types.Part.from_text(text="go")])
        async for event in runner.run_async(user_id=user, session_id="chat-1", new_message=message):
            for response in event.get_function_responses():
                responses.setdefault(response.name, []).append(response.response)
        return responses

    async def context(self, user):
        session = await self.sessions.create_session(app_name="argo", user_id=user, session_id="chat-1")
        return ToolContext(InvocationContext(
            artifact_service=self.artifacts, session_service=self.sessions,
            session=session, agent=root_agent, invocation_id=f"test-{user}",
        ))

    async def call(self, tool, context, **args):
        """Run a tool the way ADK does, between the agent's callbacks."""
        await sb.bind_sandbox(tool, args, context)
        result = await tool.run_async(args=args, tool_context=context)
        return await sb.report_reset(tool, args, context, result) or result

    async def test_runner_routes_parallel_calls_to_their_conversation(self):
        model = ScriptedModel(model="scripted", responses=[
            calls(("WriteFile", {"path": "/workspace/a.txt", "content": "alice"}),
                  ("Execute", {"command": "echo hi"})),
            calls(("save_output", {"path": "a.txt"}), ("save_output", {"path": "missing.txt"})),
            DONE,
            calls(("WriteFile", {"path": "a.txt", "content": "bob"})),
            DONE,
        ])
        alice = await self.turn(model, "alice")
        await self.turn(model, "bob")

        self.assertEqual(self.cluster.workspace("alice").data["a.txt"], b"alice")
        self.assertEqual(self.cluster.workspace("bob").data["a.txt"], b"bob")
        self.assertEqual(self.cluster.workspace("alice").runs,
                         [(["bash", "-c", "echo hi"], 30)])
        self.assertEqual(alice["Execute"][0]["stdout"], "ok\n")
        saved = next(r for r in alice["save_output"] if r["status"] == "ok")
        missing = next(r for r in alice["save_output"] if r["status"] == "error")
        self.assertEqual(saved["filename"], "a.txt")
        self.assertIn("FileNotFoundError", missing["error"])  # Reported, not raised.
        part = await self.artifacts.load_artifact(
            app_name="argo", user_id="alice", session_id="chat-1", filename="a.txt",
        )
        self.assertEqual(part.inline_data.data, b"alice")
        session = await self.sessions.get_session(app_name="argo", user_id="alice", session_id="chat-1")
        self.assertEqual(session.state[sb.UID_STATE], self.cluster.workspace("alice").uid)

    async def test_stage_skill_reset_and_release(self):
        skill = Skill(
            frontmatter=Frontmatter(name="sample", description="Test script"),
            instructions="Run sample.py", resources=Resources(
                scripts={"sample.py": Script(src="print('from skill')")},
                assets={"data.bin": b"\x00\xff"},
            ),
        )
        alice, bob = await self.context("alice"), await self.context("bob")
        skills = SkillToolset(skills=[skill], environment=sb.sandboxes, script_timeout=60)
        run_script = next(t for t in await skills.get_tools(alice) if t.name == "run_skill_script")
        stage = FunctionTool(sb.stage_artifact)
        args = {"skill_name": "sample", "file_path": "sample.py"}

        await alice.save_artifact("data.bin", types.Part.from_bytes(
            data=b"\x00\xff", mime_type="application/octet-stream",
        ))
        staged = await self.call(stage, alice, filename="data.bin")
        self.assertEqual(staged["path"], "/workspace/inputs/data.bin")
        self.assertEqual(self.cluster.workspace("alice").data["inputs/data.bin"], b"\x00\xff")
        self.assertEqual((await self.call(stage, bob, filename="data.bin"))["status"], "error")

        await self.call(run_script, alice, **args)
        self.assertIn("skills/sample/scripts/sample.py", self.cluster.workspace("alice").data)
        self.assertEqual(self.cluster.workspace("alice").runs[-1][1], 60)

        # The controller expires the claim; the next call gets a fresh one.
        first_uid = alice.state[sb.UID_STATE]
        self.cluster.sandboxes.pop(sb.claim_name("argo", "alice", "chat-1"))
        result = await self.call(run_script, alice, **args)
        self.assertEqual(result["workspace_reset"], sb.RESET_NOTICE)
        self.assertNotEqual(alice.state[sb.UID_STATE], first_uid)
        self.assertIn("skills/sample/scripts/sample.py", self.cluster.workspace("alice").data)
        self.assertNotIn("workspace_reset", await self.call(run_script, alice, **args))

        await sb.sandboxes.release("argo", "alice", "chat-1")
        self.assertEqual(self.cluster.deleted, [sb.claim_name("argo", "alice", "chat-1")])

    async def test_paths_claim_names_and_guards(self):
        self.assertEqual(sb.workspace_path(PurePosixPath("/workspace/a.py")), "a.py")
        self.assertEqual(sb.workspace_path("inputs/x.bin"), "inputs/x.bin")
        for bad in ("/tmp/x", "/workspace2/x", "/workspace", "../x", "a/../b", ""):
            with self.assertRaises(ValueError, msg=bad):
                sb.workspace_path(bad)

        name = sb.claim_name("argo", "alice@example.com", "chat-1")
        self.assertEqual(name, sb.claim_name("argo", "alice@example.com", "chat-1"))
        self.assertNotEqual(name, sb.claim_name("argo", "bob", "chat-1"))
        self.assertRegex(name, r"^argo-[0-9a-f]{20}$")

        with self.assertRaisesRegex(RuntimeError, "inside a tool call"):
            await asyncio.create_task(
                sb.sandboxes.read_file(PurePosixPath("a.txt")), context=contextvars.Context(),
            )

        async def remote(context, client_configuration):
            client_configuration.host = "https://34.1.2.3"

        with patch.dict(os.environ), patch.object(
            sb.k8s_config, "load_kube_config", new=AsyncMock(side_effect=remote),
        ) as load:
            os.environ.pop("KUBERNETES_SERVICE_HOST", None)
            with self.assertRaisesRegex(RuntimeError, "local API server"):
                await sb.SandboxEnvironment().initialize()
            self.assertEqual(load.call_args.kwargs["context"], "kind-argo-dev")


if __name__ == "__main__":
    unittest.main()
