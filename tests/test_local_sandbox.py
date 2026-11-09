"""Opt-in tests against the local kind cluster. No model or cloud calls.

Run after scripts/local-sandbox.sh up, with scripts/local-sandbox.sh tunnel running:

    ARGO_SANDBOX_INTEGRATION=1 .venv/bin/python -m unittest tests/test_local_sandbox.py -v
"""

import asyncio
import os
import shlex
import unittest
from pathlib import PurePosixPath
from unittest.mock import patch

import httpx
from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.skills.models import Frontmatter, Resources, Script, Skill
from google.adk.tools import ToolContext
from google.adk.tools.environment import EnvironmentToolset
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types
from k8s_agent_sandbox.exceptions import SandboxResponseTooLargeError
from k8s_agent_sandbox.utils import construct_sandbox_claim_lifecycle_spec
from kubernetes_asyncio.client import CoreV1Api

from argo.agent import root_agent
from argo.tools import sandbox as sb


class ScriptedModel(BaseLlm):
    responses: list

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=self.responses.pop(0)))


def calls(*specs):
    return [types.Part.from_function_call(name=name, args=args) for name, args in specs]


@unittest.skipUnless(os.getenv("ARGO_SANDBOX_INTEGRATION") == "1", "requires local kind and router tunnel")
class LocalSandboxTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await sb.sandboxes.initialize()
        self.sessions = InMemorySessionService()
        self.used = []

    async def asyncTearDown(self):
        await sb.sandboxes.initialize()  # An app lifespan in the test may have closed it.
        for s in self.used:
            await sb.sandboxes.release(s.app_name, s.user_id, s.id)
        await sb.sandboxes.close()

    async def conversation(self):
        session = await self.sessions.create_session(app_name="argo", user_id="local-test")
        self.used.append(session)
        return ToolContext(InvocationContext(
            session_service=self.sessions, session=session, agent=root_agent, invocation_id="test",
        ))

    async def sh(self, context, command, timeout=None):
        await sb.bind_sandbox(None, {}, context)  # As the agent's before_tool_callback does.
        return await sb.sandboxes.execute(command, timeout=timeout)

    async def read(self, context, path):
        await sb.bind_sandbox(None, {}, context)
        return await sb.sandboxes.read_file(PurePosixPath(path))

    async def claim(self, context):
        s = context.session
        name = sb.claim_name(s.app_name, s.user_id, s.id)
        return await sb.sandboxes.client.k8s_helper.get_sandbox_claim(name, sb.NAMESPACE)

    async def test_runtime_limits_reattach_and_isolation(self):
        one, other = await self.conversation(), await self.conversation()
        result = await self.sh(one, "printf abc | tr a-z A-Z > pipeline.txt && echo $BASH_VERSION")
        self.assertEqual(result.exit_code, 0, result)
        self.assertTrue(result.stdout.strip(), "commands run under Bash")
        self.assertEqual(await self.read(one, "pipeline.txt"), b"ABC")
        await sb.sandboxes.write_file(PurePosixPath("/workspace/nested/data.bin"), b"\x00\xffbinary")
        self.assertEqual(await self.read(one, "nested/data.bin"), b"\x00\xffbinary")

        timed_out = await self.sh(one, "echo before; sleep 10", timeout=3)
        self.assertTrue(timed_out.timed_out)
        self.assertIn("before", timed_out.stdout)
        self.assertTrue((await self.sh(one, "exec >/dev/null 2>&1; sleep 10", timeout=3)).timed_out)
        self.assertEqual((await self.sh(one, "exit 7")).exit_code, 7)
        self.assertEqual((await self.sh(one, "python - <<'PY'\nprint(6 * 7)\nPY")).stdout.strip(), "42")
        await self.sh(one, "python -c \"open('large.bin', 'wb').truncate(16 * 1024 * 1024)\"")
        with self.assertRaises(SandboxResponseTooLargeError):
            await self.read(one, "large.bin")

        # Parallel first calls (separate tasks, as ADK runs them) share one claim.
        fresh = await self.conversation()

        async def first_call():
            await self.sh(fresh, "true")
            return fresh.state[sb.UID_STATE]

        self.assertEqual(*await asyncio.gather(first_call(), first_call()))

        # A new client, as after an app restart, reattaches to the same workspace.
        await sb.sandboxes.close()
        await sb.sandboxes.initialize()
        self.assertEqual(await self.read(one, "pipeline.txt"), b"ABC")
        self.assertFalse(sb._call.get().reset)

        core = CoreV1Api(sb.sandboxes.claims.api_client)
        dns = (await core.read_namespaced_service("kube-dns", "kube-system")).spec.cluster_ip
        api = (await core.read_namespaced_service("kubernetes", "default")).spec.cluster_ip
        await self.sh(other, "true")
        other_ip = await sb._call.get().sandbox.get_pod_ip()
        code = f"""
import socket
targets = [('1.1.1.1', 443), ({api!r}, 443), ({dns!r}, 53), ({other_ip!r}, 8888), ('169.254.169.254', 80)]
for host, port in targets:
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect((host, port))
    except OSError:
        print('blocked', host, port)
    else:
        raise AssertionError(('unexpected network access', host, port))
    finally:
        s.close()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(1)
s.sendto(bytes.fromhex('123401000001000000000000076578616d706c6503636f6d0000010001'), ({dns!r}, 53))
try:
    s.recv(4096)
except OSError:
    print('blocked UDP DNS')
else:
    raise AssertionError('unexpected DNS access')
"""
        isolation = await self.sh(one, "python -c " + shlex.quote(code), timeout=20)
        self.assertEqual(isolation.exit_code, 0, isolation)
        self.assertIn("blocked UDP DNS", isolation.stdout)

    async def test_controller_expiry_and_deadline_extension(self):
        context = await self.conversation()
        await self.sh(context, "true")
        uid = context.state[sb.UID_STATE]
        s = context.session
        name = sb.claim_name(s.app_name, s.user_id, s.id)

        async def expire_in_4s():
            await sb.sandboxes.claims.patch_namespaced_custom_object(
                "extensions.agents.x-k8s.io", "v1beta1", sb.NAMESPACE, "sandboxclaims", name,
                {"spec": {"lifecycle": construct_sandbox_claim_lifecycle_spec(4)}},
                _content_type="application/merge-patch+json",
            )

        await expire_in_4s()
        await self.sh(context, "true")  # A tool call pushes the deadline out again.
        await asyncio.sleep(6)
        self.assertEqual((await self.claim(context))["metadata"]["uid"], uid)

        await expire_in_4s()
        for _ in range(30):
            if await self.claim(context) is None:
                break
            await asyncio.sleep(1)
        else:
            self.fail("The controller did not delete the expired claim")
        await self.sh(context, "true")
        self.assertTrue(sb._call.get().reset)
        self.assertNotEqual(context.state[sb.UID_STATE], uid)

    async def test_http_chat_tools_skills_restart_and_delete(self):
        # The real API, SSE and Runner, with scripted model responses instead of Vertex AI.
        with patch.dict(os.environ, {"GOOGLE_CLOUD_BUCKET": "local-test"}), patch(
            "argo.artifacts.GcsArtifactService", return_value=InMemoryArtifactService(),
        ), patch(
            "google.adk.sessions.sqlite_session_service.SqliteSessionService",
            return_value=InMemorySessionService(),
        ):
            from argo import main, routes
        from argo.artifacts import ArtifactStore

        script = """from pathlib import Path
import matplotlib.pyplot as plt
from openpyxl import Workbook
assert Path('/workspace/inputs/data.bin').read_bytes() == b'\\x00\\xff'
output = Path('/workspace/output'); output.mkdir(exist_ok=True)
plt.plot([1, 2, 3]); plt.savefig(output / 'chart.png')
book = Workbook(); book.active.append(['value', 42]); book.save(output / 'data.xlsx')
print('skill completed')
"""
        skill = Skill(frontmatter=Frontmatter(name="integration", description="Generate files"),
                      instructions="Run build.py", resources=Resources(scripts={"build.py": Script(src=script)}))
        model = ScriptedModel(model="scripted", responses=[
            calls(("stage_artifact", {"filename": "data.bin"}),
                  ("WriteFile", {"path": "/workspace/a.py", "content": "print('old')"})),
            calls(("EditFile", {"path": "a.py", "old_string": "old", "new_string": "persisted"})),
            calls(("ReadFile", {"path": "a.py"}), ("Execute", {"command": "python a.py"})),
            calls(("run_skill_script", {"skill_name": "integration", "file_path": "build.py"})),
            calls(("save_output", {"path": "output/chart.png"}),
                  ("save_output", {"path": "/workspace/output/data.xlsx"})),
            [types.Part.from_text(text="first complete")],
            calls(("Execute", {"command": "python a.py"})),
            [types.Part.from_text(text="second complete")],
        ])
        agent = root_agent.clone(update={"model": model, "tools": [
            EnvironmentToolset(environment=sb.sandboxes),
            SkillToolset(skills=[skill], environment=sb.sandboxes, script_timeout=sb.COMMAND_SECONDS),
            sb.stage_artifact, sb.save_output,
        ]})
        sessions, artifacts = InMemorySessionService(), InMemoryArtifactService()
        store = object.__new__(ArtifactStore)
        store.app_name, store.service = routes.APP_NAME, artifacts
        runner = Runner(agent=agent, app_name=routes.APP_NAME, session_service=sessions, artifact_service=artifacts)
        user = os.getenv("LOCAL_USER_ID", "aseem")

        async def load(session_id):
            return await sessions.get_session(app_name=routes.APP_NAME, user_id=user, session_id=session_id)

        def failures(session):
            return [r.response for e in session.events for r in e.get_function_responses()
                    if r.response.get("status") == "error"]

        def client():
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

        with patch.object(routes, "sessions", sessions), patch.object(routes, "artifacts", store), \
                patch.object(routes, "runner", runner):
            # Two app lifespans: the named claim and its files outlive the restart.
            async with main.lifespan(main.app), client() as http:
                response = await http.post("/argo/api/chat", json={
                    "text": "first", "attachments": [{"name": "data.bin", "data": "AP8="}],
                })
                self.assertIn("event: done", response.text, response.text)
                self.assertNotIn("event: error", response.text, response.text)
                (found,) = (await sessions.list_sessions(app_name=routes.APP_NAME, user_id=user)).sessions
                session = await load(found.id)
                self.used.append(session)
                self.assertEqual(failures(session), [])
                uid = session.state[sb.UID_STATE]
                chart = await http.get(f"/argo/api/sessions/{session.id}/artifacts/chart.png")
                self.assertTrue(chart.content.startswith(b"\x89PNG"))
            async with main.lifespan(main.app), client() as http:
                response = await http.post("/argo/api/chat", json={"session_id": session.id, "text": "second"})
                self.assertIn("persisted", response.text, response.text)
                self.assertIn("event: done", response.text, response.text)
                self.assertEqual((await load(session.id)).state[sb.UID_STATE], uid)
                self.assertEqual((await http.delete(f"/argo/api/sessions/{session.id}")).status_code, 204)
                claim = await sb.sandboxes.client.k8s_helper.get_sandbox_claim(
                    sb.claim_name(routes.APP_NAME, user, session.id), sb.NAMESPACE,
                )
                self.assertTrue(claim is None or claim["metadata"].get("deletionTimestamp"))


if __name__ == "__main__":
    unittest.main()
