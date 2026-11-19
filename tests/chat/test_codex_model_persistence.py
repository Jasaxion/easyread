"""Codex model IDs round-trip through settings and Q&A without running a CLI."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyread.app import config
from easyread.chat import chat_models
from easyread.server import settings_api


class CodexModelPersistenceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        for replacement in (
            patch.object(config, "HOME", home),
            patch.object(config, "CONFIG_PATH", home / "config.json"),
            patch.dict(os.environ, {"CODEX_HOME": str(home / "codex")}),
            patch("easyread.engines.detect.detect", return_value={"codex": {"found": True}}),
            patch("easyread.engines.engines._popen", side_effect=AssertionError("No CLI calls")),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_arbitrary_and_default_ids_persist_and_are_selectable_for_qa(self):
        for model in ("fixture-provider/custom-model", ""):
            with self.subTest(model=model):
                options = {"model": model, "reasoning_effort": "ultra", "service_tier": "fast"}
                result = settings_api.save_chat_models({
                    "models": [{"id": "custom", "engine": "codex", **options},
                               {"id": "other", "engine": "codex", "model": "other-model"}],
                    "default": "custom",
                })
                self.assertEqual(result["default"], "custom")
                self.assertEqual(result["models"][0]["model"], model)
                self.assertTrue(result["models"][0]["ready"])
                saved = settings_api.save_config({"engine": "codex", "codex": options})["config"]
                self.assertEqual(saved["codex"]["model"], model)
                reloaded = config.load()
                self.assertEqual(reloaded["chat"]["models"][0]["model"], model)
                self.assertEqual(chat_models.translation_id(reloaded), "custom")
                selected_cfg, selected = chat_models.engine_cfg(reloaded, None)
                self.assertEqual(selected["id"], "custom")
                self.assertEqual(selected_cfg["engine"], "codex")
                for key, value in options.items():
                    self.assertEqual(selected_cfg["codex"][key], value)
                other_cfg, other = chat_models.engine_cfg(reloaded, "other")
                self.assertEqual(other["id"], "other")
                self.assertEqual(other_cfg["codex"]["model"], "other-model")
                self.assertEqual(other_cfg["codex"]["reasoning_effort"], "")
                self.assertEqual(config.load()["codex"]["model"], model)


if __name__ == "__main__":
    unittest.main()
