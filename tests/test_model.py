import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from google.adk.models.anthropic_llm import AnthropicLlm, Claude
from google.adk.models.google_llm import Gemini

from src.model import load_model


class ModelLoaderTests(unittest.TestCase):
    def load(self, content, name=None):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.yaml"
            path.write_text(content)
            return load_model(name, config_path=path)

    def test_native_vertex_claude(self):
        model = load_model("claude-haiku-5-5")
        self.assertIsInstance(model, Claude)
        self.assertEqual(model.model, "projects/aseems-cloud/locations/us-east4/publishers/anthropic/models/claude-haiku-5-5")
        self.assertEqual(model._resolve_model_name(model.model), "claude-haiku-5-5")

    def test_gemini_does_not_import_litellm(self):
        import builtins
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if "litellm" in name or "lite_llm" in name:
                raise AssertionError("Gemini must not import LiteLLM")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            model = load_model("gemini-flash-latest")
        self.assertIsInstance(model, Gemini)
        self.assertEqual(model.client_kwargs, {"vertexai": False})

    def test_vertex_gemini_uses_per_model_settings(self):
        model = self.load("flash:\n  model: gemini-flash-latest\n  type: gemini\n  provider: vertex_ai\n  project: test-project\n  location: global\n")
        self.assertEqual(model.model, "gemini-flash-latest")
        self.assertEqual(model.client_kwargs, {"vertexai": True, "project": "test-project", "location": "global"})

    def test_environment_selection_and_explicit_override(self):
        with patch.dict(os.environ, {"ARGO_MODEL": "gemini-flash-latest"}):
            self.assertIsInstance(load_model(), Gemini)
            self.assertIsInstance(load_model("claude-haiku-5-5"), Claude)

    def test_direct_anthropic(self):
        model = self.load("haiku:\n  model: claude-haiku-5-5\n  type: claude\n  provider: anthropic\n")
        self.assertIsInstance(model, AnthropicLlm)
        self.assertNotIsInstance(model, Claude)

    def test_invalid_configs(self):
        cases = ["", "[]", "bad: []", "bad: {type: gemini}", "bad: {type: other, provider: unknown}", "bad: {type: gemini, provider: gemini, adapter: typo}", "bad: ["]
        with patch.dict(os.environ, {}, clear=True):
            for content in cases:
                with self.subTest(content=content), self.assertRaises(ValueError):
                    self.load(content)
            with self.assertRaisesRegex(ValueError, "project and location"):
                self.load("bad: {type: claude, provider: vertex_ai}")
            with self.assertRaisesRegex(ValueError, "Unknown model"):
                load_model("missing")


if __name__ == "__main__":
    unittest.main()
