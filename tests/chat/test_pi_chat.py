"""Pi chat selection, reasoning and explicit-Q&A isolation."""
import copy
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from easyread.app import config
from easyread.chat import chat, chat_models
from easyread.engines import engines, qa_capabilities


class PiChatTest(unittest.TestCase):
    def test_selection_reasoning_qa_and_translation_isolation(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(engine="pi", chat={"models": [
            {"id": "lean", "engine": "pi", "model": "one/shared", "reasoning_effort": "off"},
            {"id": "full", "engine": "pi", "model": "two/shared", "reasoning_effort": "max"}]},
            qa_cli={"pi": {"mode": "full"}},
            qa_model_policies={"lean": {"engine": "pi", "settings": {"mode": "default"}}})
        before = copy.deepcopy(cfg)
        calls = []
        report = Mock()
        with patch.object(chat.netcheck, "problem", return_value=None), \
                patch.object(engines, "run", side_effect=lambda options, *args: calls.append(options) or "answer"):
            for mid in ("lean", "full"):
                options, _ = chat_models.engine_cfg(cfg, mid)
                self.assertEqual(options["_qa_model_id"], mid)
                self.assertNotIn("_qa_policy", options["pi"])
                self.assertEqual(list(chat.stream(options, "question", Path("."), threading.Event(), report, qa=True)), ["answer"])
        self.assertNotIn("_qa_policy", calls[0]["pi"])
        self.assertEqual(calls[0]["pi"]["reasoning_effort"], "off")
        self.assertEqual(calls[1]["pi"]["_qa_policy"]["mode"], "full")
        self.assertIs(calls[1]["pi"]["_report_model"], report)
        translated = engines.for_translation(calls[1])
        self.assertNotIn("_qa_policy", translated["pi"])
        self.assertNotIn("_qa_model_id", translated)
        self.assertEqual(cfg, before)

    def test_sanitize_preserves_pi_off_and_display_name(self):
        items = chat_models.sanitize([{"id": "pi-off", "engine": "pi", "model": "provider/model", "reasoning_effort": "off"}])
        self.assertEqual(items[0]["reasoning_effort"], "off")
        self.assertEqual(items[0]["model"], "provider/model")
        self.assertEqual(engines.engine_name("pi"), "Pi Agent")
        self.assertIn("pi", engines.CLI_ENGINES)
        long_id = 'provider/' + 'model-' * 25
        self.assertEqual(chat_models.sanitize([{'engine': 'pi', 'model': long_id}])[0]['model'], long_id)
        with patch("easyread.app.pi_models.default_model", return_value="provider/model"):
            self.assertIn("provider/model", chat_models.label({"engine": "pi", "model": ""}))

    def test_prompt_does_not_claim_pi_sandbox_or_permission_confirmation(self):
        selected = qa_capabilities.validate("pi", {"mode": "custom"})
        guidance = qa_capabilities.prompt_guidance(selected, "pi")
        self.assertIn("no built-in sandbox or per-tool approval", guidance)
        self.assertNotIn("Respect the configured sandbox", guidance)


if __name__ == "__main__":
    unittest.main()
