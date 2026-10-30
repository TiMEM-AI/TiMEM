import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "l1policy", Path(__file__).parents[1] / "timem/memory/l1_optimization.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class L1PolicyTest(unittest.TestCase):
    def test_disabled_preserves_original_prompt(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mod.apply_l1_policy("original", "template"), "original")

    def test_explicit_artifact_and_template_binding(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "winner.json"
            p.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "policy": "Preserve dates.",
                        "base_template_sha256": hashlib.sha256(b"template").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TIMEM_L1_OPT_POLICY_PATH": str(p)}):
                self.assertTrue(
                    mod.apply_l1_policy("original", "template").endswith(
                        "Preserve dates."
                    )
                )
                with self.assertRaises(ValueError):
                    mod.apply_l1_policy("original", "different template")

    def test_invalid_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "winner.json"
            p.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "policy": "{inject}",
                        "base_template_sha256": hashlib.sha256(b"template").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"TIMEM_L1_OPT_POLICY_PATH": str(p)}),
                self.assertRaises(ValueError),
            ):
                mod.apply_l1_policy("original", "template")


class L1GeneratorIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_both_l1_paths_use_the_same_bound_policy(self):
        from timem.memory.memory_generator import MemoryGenerator

        class Prompt:
            template = "History: {previous_summary}\nDialogue: {new_dialogue}"

            def format(self, **values):
                return self.template.format(**values)

        class Manager:
            def get_prompt(self, key):
                assert key == "l1_fragment_summary"
                return Prompt()

        class FakeLLM:
            def __init__(self):
                self.prompts = []

            async def chat_stream(self, messages, **kwargs):
                self.prompts.append(messages[0].content)
                yield "A complete memory."

        instance = object.__new__(MemoryGenerator)
        instance.prompt_manager = Manager()
        instance.llm = FakeLLM()
        instance.layer_timeouts = {}
        instance._append_prompt_log = lambda *args, **kwargs: None

        async def retry(call, *args):
            return await call()

        instance._retry_with_exponential_backoff = retry
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "winner.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "policy": "Preserve dates.",
                        "base_template_sha256": hashlib.sha256(
                            Prompt.template.encode()
                        ).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TIMEM_L1_OPT_POLICY_PATH": str(path)}):
                normal = await instance.generate_l1_content("dialogue", "history")
                stream = [
                    chunk
                    async for chunk in instance.generate_l1_content_stream(
                        "dialogue", "history"
                    )
                ]
        self.assertEqual(normal, "A complete memory.")
        self.assertEqual("".join(stream), normal)
        self.assertEqual(instance.llm.prompts[0], instance.llm.prompts[1])
        self.assertTrue(instance.llm.prompts[0].endswith("Preserve dates."))


if __name__ == "__main__":
    unittest.main()
