"""Exercise artifact tools through ADK without cloud credentials or model calls."""

import base64
import json
import unittest

from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models.llm_request import LlmRequest
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.genai import types

from argo.agent import root_agent
from argo.tools import artifacts as tools


class ArtifactToolsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = InMemoryArtifactService()
        self.sessions = InMemorySessionService()
        self.context = await self.make_context()

    async def make_context(self, app="argo", user="alice", session="chat-1"):
        session = await self.sessions.create_session(
            app_name=app, user_id=user, session_id=session,
        )
        return ToolContext(InvocationContext(
            artifact_service=self.service,
            session_service=self.sessions,
            session=session,
            agent=root_agent,
            invocation_id="test",
        ))

    async def call(self, func, context=None, **args):
        return await FunctionTool(func).run_async(
            args=args, tool_context=context or self.context,
        )

    async def test_versioned_save_metadata_load_and_delete(self):
        metadata = {"description": "Draft", "tags": ["test"], "details": {"approved": False}}
        first = await self.call(
            tools.save_artifact, filename="notes.txt", content="Café\nDraft",
            custom_metadata=metadata,
        )
        second = await self.call(tools.save_artifact, filename="notes.txt", content="Final draft")
        self.assertEqual((first["version"], second["version"]), (0, 1))
        self.assertEqual(self.context.actions.artifact_delta, {"notes.txt": 1})
        self.assertEqual((await self.call(tools.list_artifacts))["filenames"], ["notes.txt"])
        self.assertEqual((await self.call(tools.list_versions, filename="notes.txt"))["versions"], [0, 1])

        original = await self.context.load_artifact("notes.txt", version=0)
        self.assertEqual(original.text, "Café\nDraft")
        info = await self.call(tools.get_artifact_version, filename="notes.txt", version=0)
        self.assertEqual(info["custom_metadata"], metadata)
        self.assertEqual((await self.call(tools.get_artifact_version, filename="notes.txt"))["version"], 1)
        versions = await self.call(tools.list_artifact_versions, filename="notes.txt")
        self.assertEqual([v["version"] for v in versions["versions"]], [0, 1])
        json.dumps(info)
        json.dumps(versions)

        # The stock loader must place saved contents into the actual model request.
        loader = next(t for t in root_agent.tools if isinstance(t, LoadArtifactsTool))
        response = await loader.run_async(
            args={"artifact_names": ["notes.txt"]}, tool_context=self.context,
        )
        request = LlmRequest(contents=[types.Content(role="user", parts=[
            types.Part.from_function_response(name="load_artifacts", response=response),
        ])])
        await loader.process_llm_request(tool_context=self.context, llm_request=request)
        self.assertIn("Final draft", [p.text for c in request.contents for p in c.parts or []])

        await self.call(tools.delete_artifact, filename="notes.txt")
        self.assertIsNone(await self.context.load_artifact("notes.txt", version=0))
        self.assertIsNone(await self.context.load_artifact("notes.txt", version=1))
        self.assertEqual((await self.call(tools.list_versions, filename="notes.txt"))["versions"], [])
        self.assertEqual((await self.call(tools.list_artifact_versions, filename="notes.txt"))["versions"], [])
        self.assertIn("error", await self.call(tools.get_artifact_version, filename="notes.txt"))
        await self.call(tools.delete_artifact, filename="notes.txt")  # Idempotent.

    async def test_scope_isolation_and_user_namespace(self):
        other_chat = await self.make_context(session="chat-2")
        other_user = await self.make_context(user="bob")
        other_app = await self.make_context(app="other")
        for context in (self.context, other_chat, other_user, other_app):
            await self.call(tools.save_artifact, context, filename="notes.txt", content="private")
        await self.call(tools.save_artifact, filename="user:shared.txt", content="shared")

        self.assertEqual(
            set((await self.call(tools.list_artifacts, other_chat))["filenames"]),
            {"notes.txt", "user:shared.txt"},
        )
        for context in (other_user, other_app):
            self.assertEqual((await self.call(tools.list_artifacts, context))["filenames"], ["notes.txt"])
        self.assertEqual(
            (await self.call(tools.get_artifact_version, other_chat, filename="user:shared.txt"))["version"], 0,
        )
        await self.call(tools.delete_artifact, filename="notes.txt")
        for context in (other_chat, other_user, other_app):
            self.assertIsNotNone(await context.load_artifact("notes.txt"))
        await self.call(tools.delete_artifact, other_chat, filename="user:shared.txt")
        self.assertIsNone(await self.context.load_artifact("user:shared.txt"))

    async def test_binary_and_utf8_round_trip_and_invalid_base64(self):
        raw = b"\x00\xff\x80\x01"
        await self.call(
            tools.save_artifact, filename="data.bin",
            content=base64.b64encode(raw).decode("ascii"),
            mime_type="application/octet-stream", base64_encoded=True,
        )
        binary = await self.context.load_artifact("data.bin")
        self.assertEqual(binary.inline_data.data, raw)
        self.assertEqual(binary.inline_data.mime_type, "application/octet-stream")
        await self.call(
            tools.save_artifact, filename="report.md", content="# Café",
            mime_type="text/markdown",
        )
        markdown = await self.context.load_artifact("report.md")
        self.assertEqual(markdown.inline_data.data.decode("utf-8"), "# Café")
        self.assertEqual(markdown.inline_data.mime_type, "text/markdown")
        for content in ("invalid!", "☃"):
            result = await self.call(
                tools.save_artifact, filename="bad.bin", content=content, base64_encoded=True,
            )
            self.assertIn("error", result)
        self.assertIsNone(await self.context.load_artifact("bad.bin"))
        self.assertNotIn("bad.bin", self.context.actions.artifact_delta)

    def test_registered_tool_schemas_hide_runtime_context(self):
        for tool in tools.artifact_tools:
            self.assertIn(tool, root_agent.tools)
            wrapped = tool if isinstance(tool, LoadArtifactsTool) else FunctionTool(tool)
            declaration = wrapped._get_declaration()
            schema = declaration.parameters_json_schema or (
                declaration.parameters.model_dump(exclude_none=True) if declaration.parameters else {}
            )
            properties = schema.get("properties", {})
            self.assertFalse({"tool_context", "app_name", "user_id", "session_id"} & properties.keys())


if __name__ == "__main__":
    unittest.main()
