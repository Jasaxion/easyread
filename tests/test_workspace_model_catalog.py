"""Workspace model metadata only: no model requests, real credentials or sessions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from easyread.app import cli_models
from easyread.workspaces import model_catalog as catalog, codex_model_list, session_cli as cli
from easyread.workspaces.sessions import Sessions, SessionError
from easyread.workspaces.store import Store


class WorkspaceModelCatalogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="easyread-catalog-test-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.codex, self.claude = self.base / "codex", self.base / "claude"
        self.codex.mkdir(); self.claude.mkdir()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.codex), "CLAUDE_CONFIG_DIR": str(self.claude)}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        exe = patch.object(cli, "executable", return_value=None)
        exe.start(); self.addCleanup(exe.stop)
        seen = patch.object(catalog.chat_models, "_seen_path", return_value=self.base / "seen.json")
        seen.start(); self.addCleanup(seen.stop)
        self.store = Store(self.base / "library")
        self.pid = self.store.allocate({"name": "Model candidates"})["id"]
        catalog._cache.clear()

    def listing(self, engine="codex", **kwargs):
        return catalog.listing(self.store, self.pid, engine, cfg=kwargs.pop("cfg", {}), **kwargs)

    def cache(self, path, *ids):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"models": [{"slug": mid, "display_name": mid.upper(), "visibility": "list"}
                                               for mid in ids]}))

    def test_explicit_catalog_authority_defaults_bounds_and_public_fields(self):
        (self.codex / "config.toml").write_text("model = 'configured'\nmodel_catalog_json = 'catalog.json'\n")
        self.cache(self.codex / "models_cache.json", "stale-cache")
        private = cli.project_home(self.store.meta(self.pid), "codex")
        self.cache(private / "models_cache.json", "stale-private")
        with patch.object(codex_model_list, "query", side_effect=AssertionError("No native query for explicit catalog")), \
                patch.object(cli, "executable", return_value="fake-codex"):
            for raw in (None, "not json", '{"models": []}', '{"models": false}', "x" * (catalog.MAX_FILE + 1)):
                with self.subTest(raw=None if raw is None else raw[:30]):
                    if raw is not None:
                        (self.codex / "catalog.json").write_text(raw)
                    result = self.listing(refresh=True)
                    self.assertEqual(result["warning"], "catalog_unavailable")
                    self.assertEqual([row["id"] for row in result["models"]], ["configured"])
                    self.assertEqual(result["default"], "configured")
            (self.codex / "catalog.json").write_text(json.dumps({"models": [
                {"slug": "visible", "display_name": "Visible", "visibility": "list", "description": "Local candidate",
                 "api_key": "secret-value", "base_url": "https://private.invalid"},
                {"slug": "visible", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"},
                {"slug": "bad\ncontrol", "visibility": "list"},
                {"slug": "safe", "display_name": "/private/config.toml", "description": "https://private.invalid", "visibility": "list"}]}))
            result = self.listing(cfg={"codex": {"model": "extra"}})
            self.assertEqual([row["id"] for row in result["models"]], ["configured", "extra", "visible", "safe"])
            self.assertEqual(result["source"], "model_catalog_json")
            self.assertEqual(result["models"][-1], {"id": "safe", "name": "safe", "source": "model_catalog_json"})
            self.assertNotIn("private", json.dumps(result)); self.assertNotIn("secret", json.dumps(result))
            for model in result["models"]:
                self.assertEqual(Sessions._model(model["id"]), model["id"])
            (self.codex / "config.toml").write_text("model_catalog_json = [")
            result = self.listing(refresh=True, cfg={"codex": {"model": "manual"}})
            self.assertEqual(result["warning"], "settings_unavailable")
            self.assertEqual([row["id"] for row in result["models"]], ["manual"])
        self.assertFalse((private / "sessions").exists())
        self.assertFalse((self.store.meta(self.pid) / "sessions/state.json").exists())

    def test_cache_paths_only_current_project_and_missing_cli_fallback(self):
        other = self.store.allocate({"name": "Other project"})["id"]
        self.cache(cli.project_home(self.store.meta(other), "codex") / "models_cache.json", "other-only")
        result = self.listing(cfg={"chat": {"models": [{"engine": "codex", "model": "custom", "name": "Custom"}]}})
        self.assertEqual([row["id"] for row in result["models"]], ["custom"])
        self.assertEqual(result["warning"], "cli_missing"); self.assertFalse(result["installed"])
        private = cli.project_home(self.store.meta(self.pid), "codex") / "models_cache.json"
        self.cache(private, "project-only")
        self.assertEqual(self.listing()["source"], "project_models_cache")
        self.cache(self.codex / "models_cache.json", "host-only")
        with patch.object(codex_model_list, "query", side_effect=AssertionError("Cached fast path")), \
                patch.object(cli, "executable", return_value="fake-codex"):
            self.assertEqual([row["id"] for row in self.listing()["models"]], ["host-only"])
        (self.codex / "models_cache.json").unlink()
        private.unlink()
        try:
            private.symlink_to(cli.project_home(self.store.meta(other), "codex") / "models_cache.json")
        except OSError:
            return
        self.assertEqual(self.listing()["models"], [])

    def test_claude_aliases_observed_settings_environment_and_validation(self):
        (self.claude / "settings.json").write_text(json.dumps({"model": "opus[1m]", "env": {
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "gateway-sonnet", "ANTHROPIC_API_KEY": "secret"}}))
        (self.base / "seen.json").write_text(json.dumps({"opus": "claude-opus-4-6", "_default": "claude-sonnet-4-6",
                                                       "_claude_version": "2.0.0", "last-default:codex": "not-claude"}))
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "env-default", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "gateway-haiku"}), \
                patch.object(cli_models, "probe_claude", side_effect=AssertionError("Never probe Claude")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("No Claude subprocess")):
            result = self.listing("claude", refresh=True, cfg={"chat": {"models": [
                {"engine": "claude", "model": "custom-native"}, {"engine": "openai", "model": "not-native"}]}})
        ids = [row["id"] for row in result["models"]]
        for mid in ("opus", "sonnet", "haiku", "opus[1m]", "claude-opus-4-6", "claude-sonnet-4-6",
                    "gateway-sonnet", "gateway-haiku", "env-default", "custom-native"):
            self.assertIn(mid, ids)
        self.assertEqual(result["default"], "env-default")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("not-native", ids); self.assertNotIn("2.0.0", ids); self.assertNotIn("not-claude", ids)
        self.assertNotIn("secret", json.dumps(result))
        for mid in ids:
            self.assertEqual(Sessions._model(mid), mid)
        for bad in ("opus[2m]", "opus[1m]suffix", "opus\n", "x;echo", "x" * 121, "[1m]", "opus[1m][1m]"):
            with self.subTest(bad=bad), self.assertRaises(SessionError):
                Sessions._model(bad)
        with patch.object(cli, "executable", return_value="fake-claude"):
            self.assertEqual(self.listing("claude")["warning"], "availability_unverified")

    def test_single_flight_refresh_ttl_failure_cache_and_configured_preservation(self):
        entered, release = threading.Event(), threading.Event()
        def query(*args):
            entered.set(); release.wait(2)
            return {"models": [{"id": "native", "name": "Native"}], "default": "native"}
        with patch.object(cli, "executable", return_value="fake-codex"), patch.object(codex_model_list, "query", side_effect=query) as native:
            with ThreadPoolExecutor(max_workers=6) as pool:
                first = pool.submit(self.listing, refresh=True)
                self.assertTrue(entered.wait(1))
                rest = [pool.submit(self.listing, refresh=True) for _ in range(5)]
                release.set()
                for future in [first, *rest]:
                    self.assertEqual(future.result()["source"], "model_list")
            self.assertEqual(native.call_count, 1)
            self.cache(self.codex / "models_cache.json", "old-disk")
            self.assertEqual(self.listing()["models"][0]["id"], "native")
            key = next(iter(catalog._cache))
            when, cached = catalog._cache[key]
            catalog._cache[key] = (when - catalog.CACHE_TTL - 1, cached)
            self.assertEqual(self.listing()["source"], "models_cache")
            native.side_effect = None
            native.return_value = {"models": [], "default": "", "warning": "model_list_timeout"}
            result = self.listing(refresh=True, cfg={"codex": {"model": "configured"}})
            self.assertEqual(result["warning"], "model_list_timeout")
            self.assertEqual([m["id"] for m in result["models"]], ["configured", "old-disk"])
            self.listing(refresh=True)
            self.assertEqual(native.call_count, 2)
        self.assertFalse((self.store.meta(self.pid) / "sessions").exists())

    def test_native_fake_protocol_pagination_failures_output_and_teardown(self):
        script = self.base / "fake_cli.py"
        log = self.base / "protocol.jsonl"
        script.write_text('''import json, os, sys, time
mode, log = sys.argv[1:]
for line in sys.stdin:
    request = json.loads(line)
    with open(log, "a") as stream:
        stream.write(json.dumps({"request": request, "cwd": os.getcwd()}) + "\\n")
    method = request["method"]
    assert method in ("initialize", "initialized", "model/list")
    if method == "initialized": continue
    if mode == "timeout": time.sleep(10)
    if mode == "output": print("x" * 300, flush=True); continue
    if mode == "error":
        print(json.dumps({"id": request["id"], "error": {"message": "secret https://private.invalid/config"}}), flush=True); continue
    if method == "initialize": result = {}
    else:
        start = 0 if request["params"]["cursor"] is None else 120
        result = {"data": [{"id": "opaque", "model": "native-" + str(i), "displayName": "Native", "isDefault": i == 0} for i in range(start, start + 120)], "nextCursor": "page-two"}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
''')
        popen = subprocess.Popen
        for mode, warning in (("ok", None), ("timeout", "model_list_timeout"), ("error", "model_list_unavailable"), ("output", "model_list_unavailable")):
            processes = []
            def start(args, **kwargs):
                self.assertEqual(args[-1], "app-server")
                self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.codex))
                self.assertFalse(Path(kwargs["cwd"]).is_relative_to(self.store.root(self.pid)))
                process = popen([sys.executable, str(script), mode, str(log)], **kwargs)
                processes.append(process)
                return process
            with self.subTest(mode=mode), patch.object(subprocess, "Popen", side_effect=start), \
                    patch.object(codex_model_list, "TIMEOUT", .3), patch.object(codex_model_list, "MAX_LINE", 256 if mode == "output" else 1024 * 1024):
                result = codex_model_list.query("fake-codex", self.codex)
                self.assertEqual(result.get("warning"), warning)
                self.assertTrue(all(p.poll() is not None for p in processes))
                self.assertNotIn("secret", json.dumps(result))
                if mode == "ok":
                    self.assertEqual(len(result["models"]), 200)
                    self.assertEqual(result["default"], "native-0")
        records = [json.loads(line) for line in log.read_text().splitlines()]
        requests = [r["request"] for r in records if r["request"]["method"] == "model/list"]
        self.assertEqual([r["params"]["limit"] for r in requests], [200, 80])
        self.assertTrue(all(not Path(record["cwd"]).exists() for record in records))
        self.assertFalse((self.codex / "config.toml").exists())
        with patch.object(subprocess, "Popen", side_effect=OSError("secret /private/config")):
            self.assertEqual(codex_model_list.query("missing-codex", self.codex), {
                "models": [], "default": "", "warning": "model_list_unavailable"})


if __name__ == "__main__":
    unittest.main()
