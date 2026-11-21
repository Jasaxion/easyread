"""Pi native metadata discovery and inert QA-only imports share upload security."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyread.app import config, pi_skills, qa_skills


def document(name):
    return f"---\nname: {name}\ndescription: Review a paper\n---\nRead the evidence.\n"


class PiSkillsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.agent = self.root / "agent"
        self.agent.mkdir()
        for mock in (patch.object(Path, "home", return_value=self.home),
                     patch.object(config, "HOME", self.root / "easyread"),
                     patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.agent),
                                             "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                                             "CODEX_HOME": str(self.home / ".codex")})):
            mock.start()
            self.addCleanup(mock.stop)

    def skill(self, path, name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document(name), encoding="utf-8")
        return path

    def test_native_agent_agents_and_configured_roots_recursive_and_excluded(self):
        a = self.skill(self.agent / "skills" / "nested" / "review" / "SKILL.md", "review")
        self.skill(self.home / ".agents" / "skills" / "common" / "SKILL.md", "common")
        extra = self.skill(self.agent / "extra" / "SKILL.md", "extra")
        self.skill(self.agent / "standalone.md", "standalone")
        (self.agent / "settings.json").write_text(json.dumps({"skills": ["extra", "standalone.md", "-extra"]}))
        items = qa_skills.inventory("pi")
        self.assertEqual({item["name"] for item in items}, {"review", "common", "extra", "standalone"})
        self.assertFalse(pi_skills.enabled(next(item for item in items if item["name"] == "extra")))
        self.assertEqual(qa_skills.resolve("pi", next(item["id"] for item in items if item["name"] == "review")), a)
        self.assertTrue(extra.exists())
        self.assertFalse((config.HOME / "qa-skills").exists())

    def test_import_security_engine_isolation_and_conflicts(self):
        native = self.skill(self.agent / "skills" / "native" / "SKILL.md", "native")
        encode = lambda name: base64.b64encode(document(name).encode()).decode()
        item = qa_skills.import_skill("pi", "SKILL.md", encode("managed"))
        self.assertTrue(item["managed"])
        self.assertTrue(Path(item["path"]).is_relative_to(config.HOME / "qa-skills" / "pi"))
        self.assertEqual(qa_skills.inventory("codex"), [])
        for name in ("native", "managed"):
            with self.assertRaisesRegex(ValueError, "already exists"):
                qa_skills.import_skill("pi", "SKILL.md", encode(name))
        with self.assertRaises(ValueError):
            qa_skills.import_skill("pi", "SKILL.md", base64.b64encode(b"unsafe no metadata").decode())
        self.assertEqual(native.read_text(), document("native"))

    def test_native_links_are_not_followed_and_scan_is_bounded(self):
        outside = self.skill(self.root / "outside" / "SKILL.md", "outside")
        root = self.agent / "skills"
        root.mkdir()
        try:
            (root / "linked").symlink_to(outside.parent, target_is_directory=True)
            (root / "linked.md").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(qa_skills.inventory("pi"), [])
        for number in range(6):
            self.skill(root / str(number) / "SKILL.md", f"skill-{number}")
        with patch.object(qa_skills, "MAX_ROOT_ENTRIES", 3):
            self.assertLessEqual(len(pi_skills.inventory()), 2)

    def test_import_rejects_native_scan_that_cannot_check_conflicts_safely(self):
        for number in range(5):
            self.skill(self.agent / 'skills' / str(number) / 'SKILL.md', f'skill-{number}')
        with patch.object(qa_skills, 'MAX_ROOT_ENTRIES', 3):
            with self.assertRaisesRegex(ValueError, 'too large'):
                qa_skills.import_skill('pi', 'SKILL.md', base64.b64encode(document('new').encode()).decode())
        self.assertFalse((config.HOME / 'qa-skills' / 'pi').exists())

    def test_managed_root_must_not_overlap_pi_native_roots(self):
        native = self.agent / "skills"
        with patch.object(config, "HOME", native):
            with self.assertRaisesRegex(ValueError, "outside native"):
                qa_skills.inventory("pi")


if __name__ == "__main__":
    unittest.main()
