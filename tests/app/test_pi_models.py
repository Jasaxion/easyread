"""Safe Pi native catalog and settings metadata; no auth values or inference."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyread.app import native_cli_settings, pi_models


TABLE = """provider  model         context  max-out  thinking  images
first     shared        200K     8K       yes       yes
second    shared        128K     4K       no        no
first     vendor/model  1M       32K      yes       no
"""


class PiModelsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.agent = self.root / "native"
        self.agent.mkdir()
        env = patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.agent)})
        env.start()
        self.addCleanup(env.stop)
        pi_models._discover.cache_clear()

    def write_settings(self, **value):
        (self.agent / "settings.json").write_text(json.dumps(value), encoding="utf-8")

    def listing(self, output=TABLE, status="ok"):
        with patch.object(pi_models.pi_cli, "path", return_value="fixture-pi"), \
                patch.object(pi_models, "_discover", return_value=(status, output)):
            return pi_models.catalog({})

    def test_qualified_models_default_and_per_model_reasoning(self):
        self.write_settings(defaultProvider="second", defaultModel="shared", defaultThinkingLevel="medium",
                            modelThinkingLevels={"first/shared": "max"}, apiKey="must-not-be-visible")
        (self.agent / "auth.json").write_text("THIS FILE MUST NOT BE READ")
        listing = self.listing()
        self.assertEqual(native_cli_settings.path("pi"), self.agent / "settings.json")
        self.assertEqual(listing["default"], "second/shared")
        self.assertEqual(listing["configured_reasoning"], "medium")
        self.assertEqual([m["id"] for m in listing["models"]], ["first/shared", "second/shared", "first/vendor/model"])
        self.assertEqual(listing["models"][0]["default_reasoning"], "max")
        self.assertEqual(listing["models"][1]["reasoning_levels"], ["off"])
        self.assertNotIn("must-not-be-visible", json.dumps(listing))
        self.assertNotIn(str(self.agent), json.dumps(listing))

    def test_native_defaults_do_not_require_running_cli_or_read_auth(self):
        self.write_settings(defaultProvider="first", defaultModel="shared", defaultThinkingLevel="off")
        with patch.object(pi_models, "_discover", side_effect=AssertionError("passive label must not spawn CLI")):
            self.assertEqual(pi_models.default_model(), "first/shared")
        listing = self.listing("", "timeout")
        self.assertEqual(listing["models"][0]["source"], "configured_default")
        self.assertEqual(listing["models"][0]["default_reasoning"], "off")
        self.assertEqual(listing["diagnostics"][-1]["status"], "timeout")
        self.assertTrue(listing["limitations"])

    def test_invalid_settings_and_unrecognized_stdout_are_safe(self):
        for raw in ("invalid", "[]", "null"):
            (self.agent / "settings.json").write_text(raw)
            listing = self.listing("not a model table: secret diagnostics")
            self.assertEqual(listing["models"], [])
            self.assertEqual(listing["diagnostics"][0]["status"], "invalid")
            self.assertNotIn("secret", json.dumps(listing))
        self.assertEqual(pi_models.parse_table("arbitrary text\nfirst model 1K 1K yes yes"), [])

    def test_bounded_discovery_uses_only_safe_native_flags(self):
        commands = []
        real_popen = subprocess.Popen
        def spawn(args, **kwargs):
            commands.append((args, kwargs))
            return real_popen([sys.executable, "-c", "print(" + repr(TABLE) + ")"], **kwargs)
        with patch.object(pi_models.subprocess, "Popen", side_effect=spawn):
            status, output = pi_models._discover("fixture", str(self.agent), 1)
        self.assertEqual(status, "ok")
        self.assertEqual(len(pi_models.parse_table(output)), 3)
        args, kwargs = commands[0]
        self.assertNotIn("--mode", args)  # Plain --list-models prints the native table.
        for flag in ("--list-models", "--offline", "--no-extensions", "--no-mcp", "--no-skills", "--no-approve", "--no-session", "--no-tools"):
            self.assertIn(flag, args)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["PI_CODING_AGENT_DIR"], str(self.agent))
        self.assertFalse(Path(kwargs["cwd"]).exists())

    def test_timeout_output_limits_and_nonzero_exit_are_safe(self):
        real_popen = subprocess.Popen
        for script, expected in [("import time; time.sleep(10)", "timeout"),
                                 ("print('x'*1000)", "too_large"),
                                 ("raise SystemExit(3)", "failed")]:
            processes = []
            def spawn(args, **kwargs):
                proc = real_popen([sys.executable, "-c", script], **kwargs)
                processes.append(proc)
                return proc
            pi_models._discover.cache_clear()
            with patch.object(pi_models.subprocess, "Popen", side_effect=spawn), \
                    patch.object(pi_models, "DISCOVERY_TIMEOUT", .1), patch.object(pi_models, "MAX_OUTPUT", 100):
                status, output = pi_models._discover("fixture", str(self.agent), 1)
            self.assertEqual(status, expected)
            self.assertEqual(output, "")
            self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
