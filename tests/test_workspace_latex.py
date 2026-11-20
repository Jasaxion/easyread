"""Unit tests use a MOCK compiler; optional real-engine tests are separate."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

from easyread.workspaces import WorkspaceError, files, latex, latex_import, latex_sync


class WorkspaceLatexTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "project"
        self.workspace.mkdir()
        self.addCleanup(latex.close)

    def wait(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = latex.status(self.workspace)
            if result["compilation"]["state"] != "running":
                return result
            time.sleep(.03)
        self.fail("Compile did not finish")

    def mock_compiler(self, code):
        """Real subprocess running Python, NOT TeX; exercises process lifecycle."""
        script = Path(self.temporary.name) / "mock-tex.py"
        script.write_text("import os,sys,time\nfrom pathlib import Path\n"
                          "output_arg = next((a.split('=', 1)[1] for a in sys.argv if a.startswith(('-output-directory=', '--output-directory='))), None)\n"
                          "output = Path(output_arg) if output_arg else Path.cwd()\n"
                          "is_tex = '-no-shell-escape' in sys.argv\n" + code)
        original = subprocess.Popen
        def popen(command, **kwargs):
            return original([sys.executable, str(script), *command], **kwargs)
        return patch.object(latex.subprocess, "Popen", side_effect=popen)

    def test_initialize_preserves_sources_and_settings_across_reopen(self):
        result = latex.initialize(self.workspace)
        self.assertEqual(result["main"], "main.tex")
        root = self.workspace / "latex-workspace"
        (root / "main.tex").write_text("user changes")
        (root / "chapters").mkdir()
        (root / "chapters/paper.tex").write_text("paper")
        latex.settings(self.workspace, {"main": "chapters/paper.tex", "engine": "xelatex"})
        result = latex.initialize(self.workspace)
        self.assertEqual((result["main"], result["engine"]), ("chapters/paper.tex", "xelatex"))
        self.assertEqual((root / "main.tex").read_text(), "user changes")
        self.assertEqual(result["compilation"]["state"], "idle")

    def test_busy_status_cancel_and_close_do_not_initialize(self):
        root = self.workspace / "latex-workspace"
        self.assertFalse(latex.busy())
        self.assertFalse(latex.busy(self.workspace))
        self.assertFalse(latex.busy(self.workspace / "not-created"))
        self.assertEqual(latex.status(self.workspace)["compilation"]["state"], "idle")
        latex.cancel(self.workspace)
        latex.close(self.workspace)
        self.assertFalse(root.exists())
        root.mkdir()
        latex.status(self.workspace)
        self.assertFalse(list(root.iterdir()))
        latex.initialize(self.workspace)
        self.assertTrue((root / "main.tex").exists())
        (root / "main.tex").unlink()
        latex.initialize(self.workspace)
        self.assertFalse((root / "main.tex").exists(), "Deleted starter must not reappear")

    def test_initialize_recovers_legacy_status_only_record(self):
        root = self.workspace / "latex-workspace"
        root.mkdir()
        (root / latex.STATE_FILE).write_text(json.dumps({"main": "main.tex", "engine": "pdflatex", "compilation": {"state": "idle", "log": "", "errors": []}}))
        latex.initialize(self.workspace)
        self.assertTrue((root / "main.tex").exists())

    def test_existing_directory_is_not_populated_or_overwritten(self):
        root = self.workspace / "latex-workspace"
        root.mkdir()
        (root / "paper.tex").write_text("existing")
        latex.initialize(self.workspace)
        self.assertFalse((root / "main.tex").exists())
        self.assertEqual((root / "paper.tex").read_text(), "existing")

    def test_settings_reject_escape_engine_and_tex_injection(self):
        latex.initialize(self.workspace)
        for path in ("../bad.tex", "/bad.tex", "C:/bad.tex", "x\\bad.tex", "x/../main.tex",
                     "main.tex\\input{secret}", "-shell-escape.tex", "a%test.tex", "a//b.tex"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                latex.settings(self.workspace, {"main": path})
        for engine in ("latex", "/usr/bin/pdflatex", "pdflatex;evil"):
            with self.assertRaises(ValueError):
                latex.settings(self.workspace, {"engine": engine})

    def test_links_and_special_files_rejected(self):
        latex.initialize(self.workspace)
        root = self.workspace / "latex-workspace"
        outside = Path(self.temporary.name) / "outside.tex"
        outside.write_text("outside")
        try:
            (root / "link.tex").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            latex.settings(self.workspace, {"main": "link.tex"})
        with patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            result = self.wait()
        self.assertEqual(result["compilation"]["state"], "error")
        self.assertIn("Unsafe LaTeX path", result["compilation"]["log"])
        (root / "link.tex").unlink()
        os.link(outside, root / "hard.tex")
        with self.assertRaises(ValueError):
            latex.settings(self.workspace, {"main": "hard.tex"})

    def test_state_and_workspace_symlinks_rejected(self):
        root = self.workspace / "latex-workspace"
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        try:
            root.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            latex.initialize(self.workspace)
        root.unlink()
        latex.initialize(self.workspace)
        (root / latex.STATE_FILE).unlink()
        (root / latex.STATE_FILE).symlink_to(outside / "state.json")
        with self.assertRaises(ValueError):
            latex.status(self.workspace)
        self.assertFalse((outside / "state.json").exists())

    def test_missing_engine_reports_install_instructions(self):
        with patch.object(latex, "_executable", return_value=None):
            result = latex.start(self.workspace)
        self.assertEqual(result["compilation"]["code"], "missing_engine")
        self.assertIn("Install TeX Live", result["compilation"]["errors"][0]["message"])
        self.assertNotIn("pdf", result["compilation"])

    def test_mock_three_engines_run_bounded_passes_with_safe_environment(self):
        code = '''assert "-no-shell-escape" in sys.argv
assert "-halt-on-error" in sys.argv
assert "-synctex=1" in sys.argv
assert os.environ["openin_any"] == "p"
assert os.environ["openout_any"] == "p"
assert "SECRET_TEST_TOKEN" not in os.environ
assert "TEXMFCNF" not in os.environ
engine = Path("expected.txt").read_text()
assert "--safer" not in sys.argv
assert ("--nosocket" in sys.argv) == (engine == "lualatex")
assert ("--nosocket" in sys.argv) == (engine == "lualatex")
p = output / "easyread.pdf"
p.write_bytes(b"%PDF-1.7 MOCK, NOT A REAL PDF")
print("mock compile")
'''
        latex.initialize(self.workspace)
        for engine in latex.ENGINES:
            (self.workspace / "latex-workspace/expected.txt").write_text(engine)
            with self.subTest(engine=engine), self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable), patch.dict(os.environ, {"SECRET_TEST_TOKEN": "secret", "TEXMFCNF": "/bad"}):
                initial = latex.start(self.workspace, {"engine": engine})
                self.assertEqual(initial["compilation"]["state"], "running")
                result = self.wait()
            self.assertEqual(result["compilation"]["state"], "done", result)
            self.assertEqual(result["compilation"]["log"].count("mock compile"), 3)
            self.assertTrue((self.workspace / result["compilation"]["pdf"]).is_file())
            self.assertEqual(latex.status(self.workspace)["engine"], engine)

    def test_mock_failure_gives_source_line_and_no_stale_pdf(self):
        with self.mock_compiler('(output / "easyread.pdf").write_bytes(b"%PDF-mock")\n'), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            self.assertEqual(self.wait()["compilation"]["state"], "done")
        code = 'print("./chapters/part.tex:12: Undefined control sequence.")\nsys.exit(1)\n'
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "error")
        self.assertNotIn("pdf", result)
        self.assertEqual(result["errors"][0], {"file": "chapters/part.tex", "line": 12, "message": "Undefined control sequence."})

    def test_mock_cancel_close_and_no_concurrent_compile(self):
        with self.mock_compiler('print("started", flush=True)\ntime.sleep(60)\n'), patch.object(latex, "_executable", return_value=sys.executable):
            first = latex.start(self.workspace)
            second = latex.start(self.workspace)
            self.assertEqual(first["compilation"]["started"], second["compilation"]["started"])
            self.assertTrue(latex.busy())
            self.assertTrue(latex.busy(self.workspace))
            self.assertFalse(latex.busy(self.workspace / "another"))
            with self.assertRaises(ValueError):
                latex.settings(self.workspace, {"engine": "xelatex"})
            time.sleep(.2)
            result = latex.cancel(self.workspace)["compilation"]
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["code"], "cancelled")
        self.assertNotIn("pdf", result)
        self.assertFalse(latex.busy())
        self.assertFalse(latex.busy(self.workspace))

    def test_mock_timeout_and_output_limit(self):
        with self.mock_compiler('time.sleep(60)\n'), patch.object(latex, "_executable", return_value=sys.executable), patch.dict(os.environ, {"EASYREAD_TEX_TIMEOUT": "5"}):
            latex.start(self.workspace)
            self.assertIn("exceeded 5 seconds", self.wait()["compilation"]["log"])
        with self.mock_compiler('sys.stdout.write("x" * (3 * 1024 * 1024))\n'), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "error")
        self.assertIn("exceeded 2 MiB", result["log"])
        self.assertLessEqual(len(result["log"]), latex.MAX_LOG)

    def test_mock_fast_output_overflow_outside_output_directory_is_rejected(self):
        with self.mock_compiler('Path("oversized.bin").write_bytes(b"x" * 4096)\n'), patch.object(latex, "_executable", return_value=sys.executable), patch.object(latex, "MAX_OUTPUT", 2048):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "error")
        self.assertIn("output limits", result["log"])
        self.assertNotIn("pdf", result)

    def test_initialize_status_settings_never_launch_subprocess(self):
        with patch.object(latex.subprocess, "Popen") as popen:
            latex.initialize(self.workspace)
            latex.status(self.workspace)
            latex.settings(self.workspace, {"engine": "lualatex"})
            popen.assert_not_called()

    def test_build_directory_symlink_rejected(self):
        latex.initialize(self.workspace)
        outside = Path(self.temporary.name) / "outside-build"
        outside.mkdir()
        try:
            (self.workspace / "latex-workspace" / latex.BUILD_DIR).symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with patch.object(latex, "_executable", return_value=sys.executable), patch.object(latex.subprocess, "Popen") as popen:
            latex.start(self.workspace)
            result = self.wait()["compilation"]
            popen.assert_not_called()
        self.assertEqual(result["state"], "error")
        self.assertFalse(list(outside.iterdir()))

    def test_mock_retention_keeps_three_marked_builds_and_user_directories(self):
        latex.initialize(self.workspace)
        builds = self.workspace / "latex-workspace" / latex.BUILD_DIR
        builds.mkdir()
        user = builds / ("a" * 32)
        user.mkdir()
        (user / "keep.txt").write_text("user-owned")
        named = builds / "my-results"
        named.mkdir()
        for _ in range(4):
            with self.mock_compiler('(output / "easyread.pdf").write_bytes(b"%PDF-mock")\n'), patch.object(latex, "_executable", return_value=sys.executable):
                latex.start(self.workspace)
                result = self.wait()["compilation"]
            self.assertEqual(result["state"], "done", result)
        self.assertEqual(len(list(builds.glob("*.json"))), 3)
        self.assertEqual(len([p for p in builds.iterdir() if p.is_dir()]), 5)
        self.assertEqual((user / "keep.txt").read_text(), "user-owned")
        self.assertTrue((self.workspace / result["pdf"]).is_file())

    def bibliography_source(self):
        latex.initialize(self.workspace)
        (self.workspace / "latex-workspace/references.bib").write_text('@book{key,author={Author},title={A Research Result},year={2026}}')

    def test_mock_bibtex_runs_between_tex_passes(self):
        self.bibliography_source()
        code = '''if is_tex:
    if (output / "easyread.aux").exists():
        assert (output / "easyread.bbl").read_text() == "MOCK bibliography"
    (output / "easyread.aux").write_text(r"\\citation{key}" + "\\n" + r"\\bibdata{references}" + "\\n" + r"\\bibstyle{plain}")
    (output / "easyread.pdf").write_bytes(b"%PDF-mock")
else:
    assert sys.argv[-1] == "easyread"
    assert os.environ["openin_any"] == "p"
    assert os.environ["BIBINPUTS"].endswith("//" + os.pathsep)
    (output / "easyread.bbl").write_text("MOCK bibliography")
    print("MOCK BibTeX")
'''
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(result["bibliography"], "bibtex")
        self.assertEqual(result["log"].count("MOCK BibTeX"), 1)
        self.assertLess(result["log"].index("MOCK BibTeX"), result["log"].index("pass 2/3"))

    def test_mock_biber_uses_noconf_and_local_resources(self):
        self.bibliography_source()
        bcf = '<bcf:controlfile xmlns:bcf="https://sourceforge.net/projects/biblatex"><bcf:bibdata><bcf:datasource type="file" datatype="bibtex" glob="false">references.bib</bcf:datasource></bcf:bibdata></bcf:controlfile>'
        code = f'''if is_tex:
    if (output / "easyread.bcf").exists():
        assert (output / "easyread.bbl").read_text() == "MOCK Biber bibliography"
    (output / "easyread.bcf").write_text({bcf!r})
    (output / "easyread.pdf").write_bytes(b"%PDF-mock")
else:
    assert "--noconf" in sys.argv
    assert any(a.startswith("--input-directory=") for a in sys.argv)
    assert "PERL5OPT" not in os.environ
    (output / "easyread.bbl").write_text("MOCK Biber bibliography")
'''
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable), patch.dict(os.environ, {"PERL5OPT": "unsafe"}):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(result["bibliography"], "biber")

    def test_mock_missing_bibtex_and_bibtex_error_do_not_advertise_pdf(self):
        self.bibliography_source()
        code = '''if is_tex:
    (output / "easyread.aux").write_text(r"\\bibdata{references}" + "\\n" + r"\\bibstyle{plain}")
    (output / "easyread.pdf").write_bytes(b"%PDF-stale")
else:
    print("Syntax error---line 4 of file references.bib")
    sys.exit(2)
'''
        with self.mock_compiler(code), patch.object(latex, "_executable", side_effect=lambda name: None if name == "bibtex" else sys.executable):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["code"], "missing_bibliography_tool")
        self.assertNotIn("pdf", result)
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertEqual(result["state"], "error")
        self.assertNotIn("pdf", result)
        self.assertEqual(result["errors"][0]["file"], "references.bib")
        self.assertEqual(result["errors"][0]["line"], 4)

    def test_mock_bibliography_cancellation_and_timeout_share_job_budget(self):
        self.bibliography_source()
        code = '''if is_tex:
    (output / "easyread.aux").write_text(r"\\bibdata{references}" + "\\n" + r"\\bibstyle{plain}")
    (output / "easyread.pdf").write_bytes(b"%PDF-stale")
else:
    time.sleep(60)
'''
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable):
            latex.start(self.workspace)
            deadline = time.monotonic() + 5
            while latex.status(self.workspace)["compilation"].get("bibliography") != "bibtex" and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertTrue(latex.busy(self.workspace))
            result = latex.cancel(self.workspace)["compilation"]
        self.assertEqual(result["code"], "cancelled")
        self.assertNotIn("pdf", result)
        with self.mock_compiler(code), patch.object(latex, "_executable", return_value=sys.executable), patch.dict(os.environ, {"EASYREAD_TEX_TIMEOUT": "5"}):
            latex.start(self.workspace)
            result = self.wait()["compilation"]
        self.assertIn("exceeded 5 seconds", result["log"])
        self.assertNotIn("pdf", result)

    def test_bibliography_controls_reject_unsafe_paths_remote_xml_and_options(self):
        self.bibliography_source()
        build = self.workspace / "latex-workspace"
        output = build / "test-output"
        output.mkdir()
        for directive in (r"\\bibdata{../outside}", r"\\bibstyle{../../style}", r"\\@input{/tmp/outside.aux}", r"\\@input{../outside{bad}}"):
            (output / "easyread.aux").write_text(directive)
            with self.subTest(directive=directive), self.assertRaises((ValueError, OSError)):
                latex._bibliography_tool(build, output)
        unsafe_xml = [
            '<bcf:bibdata><bcf:datasource type="file" datatype="bibtex">https://example.com/remote.bib</bcf:datasource></bcf:bibdata>',
            '<bcf:bibdata><bcf:datasource type="file" datatype="bibtex">../private.bib</bcf:datasource></bcf:bibdata>',
            '<bcf:bibdata><bcf:datasource type="file" datatype="bibtex" glob="true">references.bib</bcf:datasource></bcf:bibdata>',
            '<bcf:bibdata><bcf:datasource type="file" datatype="ris">references.bib</bcf:datasource></bcf:bibdata>',
            '<bcf:options component="biber" type="global"><bcf:option><bcf:key>output_directory</bcf:key><bcf:value>/outside</bcf:value></bcf:option></bcf:options>',
            '<outside xmlns="http://www.w3.org/2001/XInclude" href="/etc/passwd"/>',
        ]
        for body in unsafe_xml:
            (output / "easyread.bcf").write_text('<bcf:controlfile xmlns:bcf="https://sourceforge.net/projects/biblatex">' + body + '</bcf:controlfile>')
            with self.subTest(body=body), self.assertRaises(ValueError):
                latex._bibliography_tool(build, output)
        (output / "easyread.bcf").write_text('<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>')
        with self.assertRaises(ValueError):
            latex._bibliography_tool(build, output)

    def test_zip_validation_conflicts_and_rollback_preserve_sources(self):
        latex.initialize(self.workspace)
        root = self.workspace / "latex-workspace"
        original = (root / "main.tex").read_bytes()
        state = (root / latex.STATE_FILE).read_bytes()
        (root / "keep.txt").write_text("unrelated")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            executable = zipfile.ZipInfo("paper/main.tex")
            executable.external_attr = 0o100777 << 16
            archive.writestr(executable, "new source")
            archive.writestr("paper/sections/a.tex", "section")
        with self.assertRaises(WorkspaceError) as conflict:
            latex_import.import_zip(self.workspace, output.getvalue())
        self.assertEqual((conflict.exception.status, conflict.exception.code), (409, "file_conflict"))
        publish = files.publish
        def fail_settings(path, *args, **kwargs):
            if path.name == latex.STATE_FILE:
                raise OSError("injected settings failure")
            return publish(path, *args, **kwargs)
        with patch.object(files, "publish", side_effect=fail_settings), self.assertRaises(WorkspaceError):
            latex_import.import_zip(self.workspace, output.getvalue(), replace=True)
        self.assertEqual((root / "main.tex").read_bytes(), original)
        self.assertEqual((root / latex.STATE_FILE).read_bytes(), state)
        self.assertFalse((root / "sections").exists())
        with patch.object(latex.subprocess, "Popen") as process:
            result = latex_import.import_zip(self.workspace, output.getvalue(), replace=True)
            process.assert_not_called()
        self.assertEqual(result["main"], "main.tex")
        self.assertEqual(result["compilation"]["state"], "idle")
        self.assertEqual((root / "keep.txt").read_text(), "unrelated")
        self.assertEqual((root / "main.tex").read_text(), "new source")
        self.assertEqual((root / "main.tex").stat().st_mode & 0o111, 0)
        (root / "sections/a.tex").unlink()
        (root / "sections/a.tex").mkdir()
        with self.assertRaises(WorkspaceError) as conflict:
            latex_import.import_zip(self.workspace, output.getvalue(), replace=True)
        self.assertEqual(conflict.exception.status, 409)

    def test_zip_rejects_traversal_links_duplicates_and_expansion_before_mutation(self):
        for names in (("../escape.tex",), ("/absolute.tex",), ("a.tex", "A.tex"), ("a", "a/main.tex"),
                      ("wrapper/.easyread-build/main.tex",), ("x\\main.tex",)):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for name in names:
                    archive.writestr(name, "\\documentclass{article}")
            with self.subTest(names=names), self.assertRaises(WorkspaceError):
                latex_import.import_zip(self.workspace, output.getvalue())
            self.assertFalse((self.workspace / "latex-workspace").exists())
        for mode in (0o120777, 0o010644):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                info = zipfile.ZipInfo("main.tex")
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, "outside")
            with self.assertRaises(WorkspaceError):
                latex_import.import_zip(self.workspace, output.getvalue())
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("main.tex", "x" * 1024)
        with patch.object(latex, "MAX_SOURCE", 512), self.assertRaises(WorkspaceError) as limit:
            latex_import.import_zip(self.workspace, output.getvalue())
        self.assertEqual(limit.exception.status, 413)
        self.assertFalse((self.workspace / "latex-workspace").exists())

    def test_restart_marks_running_as_interrupted_and_validates_pdf(self):
        latex.initialize(self.workspace)
        path = self.workspace / "latex-workspace" / latex.STATE_FILE
        state = json.loads(path.read_text())
        state["compilation"] = {"state": "running", "log": "old log", "pdf": "../outside.pdf"}
        path.write_text(json.dumps(state))
        result = latex.status(self.workspace)["compilation"]
        self.assertEqual(result["code"], "interrupted")
        self.assertNotIn("pdf", result)


class WorkspaceLatexRealEngineTest(unittest.TestCase):
    def test_real_zip_compile_synctex_top_left_and_snapshot_versions(self):
        installed = [engine for engine in latex.ENGINES if shutil.which(engine)]
        if not installed or not shutil.which("synctex"):
            self.skipTest("Real TeX/SyncTeX unavailable; run in the disposable TeX image")
        import pdfplumber
        source = ("\\documentclass{article}\n\\begin{document}\n"
                  "\\noindent TOPMARKER first line.\n\\par\n\\vspace{220pt}\n"
                  "\\input{sections/a}\n\\newpage\nSECOND page marker.\n\\end{document}\n")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("wrapped/article.tex", source)
            archive.writestr("wrapped/sections/a.tex", "\\noindent LOWMARKER included source.\n")
        for engine in installed:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                result = latex_import.import_zip(workspace, output.getvalue())
                self.assertEqual(result["main"], "article.tex")
                self.assertFalse(latex.busy(workspace))
                latex.start(workspace, {"engine": engine})
                deadline = time.monotonic() + 130
                while latex.busy(workspace) and time.monotonic() < deadline:
                    time.sleep(.1)
                result = latex.status(workspace)["compilation"]
                latex.close(workspace)
                self.assertEqual(result["state"], "done", result)
                self.assertTrue(result["synctex"])
                pdf = result["pdf"]
                with pdfplumber.open(workspace / pdf) as document:
                    page = document.pages[0]
                    words = {word["text"]: word for word in page.extract_words()}
                    for marker, path, line in (("TOPMARKER", "article.tex", 3), ("LOWMARKER", "sections/a.tex", 1)):
                        found = latex_sync.forward(workspace, {"pdf": pdf, "path": "latex-workspace/" + path, "line": line})
                        self.assertTrue(found["found"], found)
                        match, word = found["matches"][0], words[marker]
                        self.assertEqual(match["page"], 1)
                        self.assertAlmostEqual(match["page_height"], page.height, places=2)
                        self.assertLess(abs(match["x"] - word["x0"]), 2)
                        # Prove actual glyph overlap in TOP-LEFT PDF points, not
                        # just a round trip through two equally wrong transforms.
                        self.assertLess(match["y"], word["bottom"])
                        self.assertGreater(match["y"] + match["height"], word["top"])
                        # XeTeX records glue after words rather than individual
                        # glyphs; use the interior word for its included-file hit.
                        hit = words["included"] if marker == "LOWMARKER" else word
                        inverse = latex_sync.inverse(workspace, {"pdf": pdf, "page": 1,
                            "x": (hit["x0"] + hit["x1"]) / 2, "y": (hit["top"] + hit["bottom"]) / 2})
                        self.assertEqual(inverse["path"], "latex-workspace/" + path)
                        self.assertEqual(inverse["line"], line)
                second = latex_sync.forward(workspace, {"pdf": pdf, "path": "latex-workspace/article.tex", "line": 8})
                self.assertTrue(second["found"], second)
                self.assertEqual(second["matches"][0]["page"], 2)
                target = workspace / "latex-workspace/sections/a.tex"
                target.write_text("changed")
                with self.assertRaises(WorkspaceError) as stale:
                    latex_sync.forward(workspace, {"pdf": pdf, "path": "latex-workspace/sections/a.tex", "line": 1})
                self.assertEqual((stale.exception.status, stale.exception.code), (409, "stale_source"))
                with (workspace / pdf).open("ab") as stream:
                    stream.write(b"tampered")
                with self.assertRaises(WorkspaceError) as stale:
                    latex_sync.inverse(workspace, {"pdf": pdf, "page": 1, "x": 1, "y": 1})
                self.assertEqual((stale.exception.status, stale.exception.code), (409, "stale_pdf"))

    def test_real_bibtex_and_biber_bibliography_text_in_pdf(self):
        if not all(shutil.which(tool) for tool in ("pdflatex", "bibtex", "biber")):
            self.skipTest("Real pdflatex/BibTeX/Biber unavailable; MOCK tests are not bibliography acceptance")
        from pypdf import PdfReader
        for tool in ("bibtex", "biber"):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                latex.initialize(workspace)
                root = workspace / "latex-workspace"
                (root / "references.bib").write_text('@book{key,author={Research, Ada},title={Bibliography acceptance result},year={2026},publisher={EasyRead}}')
                preamble = "\\usepackage[backend=biber]{biblatex}\n\\addbibresource{references.bib}\n" if tool == "biber" else ""
                ending = "\\printbibliography" if tool == "biber" else "\\bibliographystyle{plain}\n\\bibliography{references}"
                (root / "main.tex").write_text("\\documentclass{article}\n" + preamble + "\\begin{document}\nCitation \\cite{key}.\n" + ending + "\n\\end{document}\n")
                latex.start(workspace)
                deadline = time.monotonic() + 130
                while latex.busy(workspace) and time.monotonic() < deadline:
                    time.sleep(.1)
                result = latex.status(workspace)["compilation"]
                latex.close(workspace)
                self.assertEqual(result["state"], "done", result)
                self.assertEqual(result["bibliography"], tool)
                text = " ".join(" ".join(page.extract_text() or "" for page in PdfReader(workspace / result["pdf"]).pages).split())
                self.assertIn("Bibliography acceptance result", text)

    def test_real_installed_engines_compile_pdf_and_errors(self):
        installed = [engine for engine in latex.ENGINES if shutil.which(engine)]
        if not installed:
            self.skipTest("No real TeX engine installed; MOCK tests are not compile acceptance")
        for engine in installed:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                latex.start(workspace, {"engine": engine})
                deadline = time.monotonic() + 130
                while latex.status(workspace)["compilation"]["state"] == "running" and time.monotonic() < deadline:
                    time.sleep(.1)
                result = latex.status(workspace)["compilation"]
                latex.close(workspace)
                self.assertEqual(result["state"], "done", result)
                self.assertTrue((workspace / result["pdf"]).read_bytes().startswith(b"%PDF-"))
                (workspace / "latex-workspace/main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\badcommand\n\\end{document}\n")
                latex.start(workspace)
                deadline = time.monotonic() + 130
                while latex.status(workspace)["compilation"]["state"] == "running" and time.monotonic() < deadline:
                    time.sleep(.1)
                result = latex.status(workspace)["compilation"]
                latex.close(workspace)
                self.assertEqual(result["state"], "error")
                self.assertNotIn("pdf", result)
                self.assertEqual(result["errors"][0]["line"], 3)


if __name__ == "__main__":
    unittest.main()
