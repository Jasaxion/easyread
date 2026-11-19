"""Codex model catalogs are read locally; these tests never invoke a model."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyread.app import cli_models


class CodexCatalogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.write_catalog("models_cache.json", [self.model("cached-model")])

    @staticmethod
    def model(slug, **extra):
        return {"slug": slug, "visibility": "list", **extra}

    def write_catalog(self, filename, models):
        path = self.home / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"models": models}), encoding="utf-8")
        return path

    def use_catalog(self, path):
        # Literal TOML strings and comments must be parsed, not treated as path text.
        (self.home / "config.toml").write_text(
            "model = 'gateway-model'\nmodel_reasoning_effort = 'high'\n"
            f"model_catalog_json = '{path}' # local catalog\n", encoding="utf-8")

    def assert_default_fallback(self, listing, status):
        self.assertEqual(listing["models"], [{
            "id": "gateway-model", "name": "gateway-model", "desc": "",
            "source": "configured_default", "reasoning_levels": [], "default_reasoning": "",
        }])
        self.assertEqual(listing["diagnostics"], [
            {"source": "config", "status": "ok"},
            {"source": "model_catalog_json", "status": status},
        ])

    def test_cache_without_custom_catalog(self):
        listing = cli_models.codex()
        self.assertEqual([m["id"] for m in listing["models"]], ["cached-model"])
        self.assertEqual(listing["diagnostics"], [
            {"source": "config", "status": "missing"}, {"source": "models_cache", "status": "ok"},
        ])

    def test_custom_catalog_replaces_cache_and_preserves_metadata(self):
        path = self.write_catalog("gateway-models.json", [
            self.model("later", priority=9),
            self.model("hidden", visibility="hide", priority=0),
            self.model("gateway-model", priority=1, display_name="Gateway Model",
                       description="Custom provider model", default_reasoning_level="high",
                       supported_reasoning_levels=[{"effort": "high"}, {"effort": "ultra"}]),
        ])
        self.use_catalog(path)
        original = (self.home / "config.toml").read_bytes()
        listing = cli_models.codex()
        self.assertEqual([m["id"] for m in listing["models"]], ["gateway-model", "later"])
        self.assertEqual(listing["default"], "gateway-model")
        self.assertEqual(listing["configured_reasoning"], "high")
        self.assertEqual(listing["diagnostics"], [
            {"source": "config", "status": "ok"}, {"source": "model_catalog_json", "status": "ok"},
        ])
        self.assertEqual(listing["models"][0], {
            "id": "gateway-model", "name": "Gateway Model", "desc": "Custom provider model",
            "reasoning_levels": ["high", "ultra"], "default_reasoning": "high",
        })
        self.assertEqual((self.home / "config.toml").read_bytes(), original)

    def test_relative_catalog_is_relative_to_config_directory(self):
        self.write_catalog("catalogs/models.json", [self.model("relative-model")])
        self.use_catalog("catalogs/models.json")
        self.assertEqual([m["id"] for m in cli_models.codex()["models"]], ["relative-model"])

    def test_empty_custom_catalog_does_not_restore_cached_models(self):
        self.use_catalog(self.write_catalog("empty.json", []))
        self.assert_default_fallback(cli_models.codex(), "empty")

    def test_missing_custom_catalog_does_not_restore_cached_models(self):
        self.use_catalog(self.home / "missing.json")
        self.assert_default_fallback(cli_models.codex(), "missing")

    def test_invalid_custom_catalog_is_safe(self):
        self.use_catalog(self.home / "invalid.json")
        for raw in ("not json", "null", "[]", '{"models": null}', '{"models": 1}'):
            with self.subTest(raw=raw):
                (self.home / "invalid.json").write_text(raw, encoding="utf-8")
                self.assert_default_fallback(cli_models.codex(), "invalid")

    def test_invalid_entries_do_not_hide_valid_models(self):
        path = self.write_catalog("mixed.json", [None, 42, "bad", {},
            self.model(42), self.model("valid", supported_reasoning_levels=None)])
        self.use_catalog(path)
        self.assertEqual([m["id"] for m in cli_models.codex()["models"]], ["valid"])

    def test_malformed_reasoning_metadata_does_not_hide_other_models(self):
        path = self.write_catalog("mixed.json", [
            self.model("bad-levels", supported_reasoning_levels=42),
            self.model("good", supported_reasoning_levels=[{"effort": "ultra"}, {"effort": 42}, None]),
        ])
        self.use_catalog(path)
        models = cli_models.codex()["models"]
        self.assertEqual([m["id"] for m in models], ["bad-levels", "good"])
        self.assertEqual(models[0]["reasoning_levels"], [])
        self.assertEqual(models[1]["reasoning_levels"], ["ultra"])

    def test_rereads_catalog_without_restarting(self):
        path = self.write_catalog("gateway.json", [self.model("before")])
        self.use_catalog(path)
        self.assertEqual(cli_models.codex()["models"][0]["id"], "before")
        self.write_catalog("gateway.json", [self.model("after")])
        self.assertEqual(cli_models.codex()["models"][0]["id"], "after")

    def test_invalid_config_does_not_break_model_settings_or_use_cache(self):
        (self.home / "config.toml").write_text("model_catalog_json = [", encoding="utf-8")
        listing = cli_models.codex()
        self.assertEqual(listing["models"], [])
        self.assertEqual(listing["diagnostics"], [{"source": "config", "status": "invalid"}])

    def test_no_catalog_or_cache_uses_root_config_default_under_home(self):
        codex_home = self.home / ".codex"
        codex_home.mkdir()
        config_path = codex_home / "config.toml"
        config_path.write_text("model = 'configured-model'\nmodel_provider = 'fixture-provider'\n"
                               "model_reasoning_effort = 'high'\nservice_tier = 'fast'\n"
                               "[profiles.other]\nmodel = 'not-the-default'\n", encoding="utf-8")
        original = config_path.read_bytes()
        with patch.dict(os.environ), patch.object(Path, "home", return_value=self.home), \
                patch.object(cli_models.engines, "_popen", side_effect=AssertionError("No CLI discovery")):
            os.environ.pop("CODEX_HOME", None)
            listing = cli_models.codex()
        self.assertEqual(listing["default"], "configured-model")
        self.assertEqual([m["id"] for m in listing["models"]], ["configured-model"])
        self.assertEqual(listing["models"][0]["source"], "configured_default")
        self.assertEqual(listing["configured_reasoning"], "high")
        self.assertEqual(listing["configured_tier"], "fast")
        self.assertEqual(listing["diagnostics"], [
            {"source": "config", "status": "ok"}, {"source": "models_cache", "status": "missing"},
        ])
        self.assertEqual(config_path.read_bytes(), original)
        self.assertEqual(list(codex_home.iterdir()), [config_path])

    def test_missing_all_sources_has_diagnostics_and_no_invented_models(self):
        (self.home / "models_cache.json").unlink()
        listing = cli_models.codex()
        self.assertEqual(listing["default"], "")
        self.assertEqual(listing["models"], [])
        self.assertEqual(listing["diagnostics"], [
            {"source": "config", "status": "missing"}, {"source": "models_cache", "status": "missing"},
        ])

    def test_cache_failure_diagnostics_identify_the_selected_source(self):
        cache = self.home / "models_cache.json"
        for raw, status in [("not json", "invalid"), ('{"models": []}', "empty"),
                            ('{"models": [{"slug": "hidden", "visibility": "hide"}]}', "filtered_empty")]:
            with self.subTest(status=status):
                cache.write_text(raw, encoding="utf-8")
                listing = cli_models.codex()
                self.assertEqual(listing["models"], [])
                self.assertEqual(listing["diagnostics"][-1], {"source": "models_cache", "status": status})
        # No config exists, so the only attempted read is the cache.
        with patch.object(Path, "read_text", side_effect=PermissionError("fixture-only")):
            listing = cli_models.codex()
        self.assertEqual(listing["diagnostics"][-1], {"source": "models_cache", "status": "unreadable"})

    def test_filtered_empty_is_distinct_from_empty_catalog(self):
        self.use_catalog(self.write_catalog("hidden.json", [self.model("hidden", visibility="hide"),
                                                           self.model(" "), None, {}]))
        self.assert_default_fallback(cli_models.codex(), "filtered_empty")

    def test_unreadable_sources_have_safe_diagnostics(self):
        self.use_catalog(self.write_catalog("gateway.json", [self.model("gateway-model")]))
        read_text = Path.read_text
        for filename, source in [("config.toml", "config"), ("gateway.json", "model_catalog_json")]:
            with self.subTest(source=source):
                def fail_source(path, *args, **kwargs):
                    if path.name == filename:
                        raise PermissionError("fixture-sensitive-path-and-content")
                    return read_text(path, *args, **kwargs)
                with patch.object(Path, "read_text", fail_source):
                    listing = cli_models.codex()
                self.assertEqual(listing["diagnostics"][-1], {"source": source, "status": "unreadable"})
                self.assertNotIn("fixture-sensitive", json.dumps(listing))
                self.assertNotIn(str(self.home), json.dumps(listing))
                if source == "config":
                    self.assertEqual(listing["models"], [])
                else:
                    self.assert_default_fallback(listing, "unreadable")

    def test_invalid_catalog_path_types_do_not_read_cache(self):
        for value in ("42", "false", "[]", "{}"):
            with self.subTest(value=value):
                (self.home / "config.toml").write_text(
                    "model = 'gateway-model'\nmodel_catalog_json = " + value, encoding="utf-8")
                self.assert_default_fallback(cli_models.codex(), "invalid")

    def test_invalid_utf8_sources_are_safe(self):
        self.use_catalog(self.home / "broken.json")
        (self.home / "broken.json").write_bytes(b"\xff")
        self.assert_default_fallback(cli_models.codex(), "invalid")
        (self.home / "config.toml").write_bytes(b"\xff")
        self.assertEqual(cli_models.codex()["diagnostics"], [{"source": "config", "status": "invalid"}])

    def test_config_strings_do_not_include_nested_settings_or_sensitive_fields(self):
        (self.home / "config.toml").write_text(
            "model = 42\nmodel_reasoning_effort = false\nservice_tier = []\n"
            "[model_providers.fixture]\nmodel = 'not-root'\napi_key = 'fixture-sensitive-marker'\n",
            encoding="utf-8")
        listing = cli_models.codex()
        self.assertEqual(listing["default"], "")
        self.assertEqual(listing["configured_reasoning"], "")
        self.assertEqual(listing["configured_tier"], "")
        self.assertNotIn("fixture-sensitive", json.dumps(listing))
        self.assertNotIn("not-root", json.dumps(listing))

    def test_full_listing_preserves_other_engines(self):
        self.use_catalog(self.write_catalog("gateway.json", [self.model("gateway-model")]))
        with patch.object(Path, "home", return_value=self.home), \
                patch("easyread.app.config.load", return_value={"agy": {}}), \
                patch("easyread.app.agent_models.antigravity", return_value={}), \
                patch.object(cli_models.chat_models, "actual_of", return_value=""), \
                patch.dict(os.environ, {"CLAUDE_CODE_EFFORT_LEVEL": "high"}):
            listing = cli_models.listing()
        self.assertEqual(listing["codex"]["models"][0]["id"], "gateway-model")
        self.assertEqual(listing["claude"]["configured_reasoning"], "high")
        self.assertEqual([m["id"] for m in listing["claude"]["models"]], ["opus", "sonnet", "haiku"])


if __name__ == "__main__":
    unittest.main()
