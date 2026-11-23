import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from helpers import PAGE, SCRIPTS, StatusBlockFixture, wl


class StatusLinkRoots(StatusBlockFixture):
    def test_backticked_directory_paths_resolve_in_the_repository(self):
        wl.write_text(self.page, PAGE.replace(
            "- **Next:** nothing",
            "- **Next:** Check `src/`, `src/nested/`, `removed/`, `src/removed/`, and `src/file/`"))
        with tempfile.TemporaryDirectory() as git_root:
            os.makedirs(os.path.join(git_root, "src", "nested"))
            wl.write_text(os.path.join(git_root, "src", "file"), "regular file\n")
            run = self.run_tool("audit.py", "--no-git", WAYMARK_GIT_ROOT=git_root)

        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        warnings = [line.strip() for line in run.stdout.splitlines() if line.strip().startswith("WARN  W6")]
        self.assertCountEqual(warnings, [
            "WARN  W6 demo.md: repo path does not exist: src/removed/",
            "WARN  W6 demo.md: repo path does not exist: src/file/",
        ])

    def test_missing_local_wiki_link_in_status_is_still_an_error(self):
        wl.write_text(self.page, PAGE.replace("- **Next:** nothing", "- **Next:** [evidence](missing-topic.md)"))
        run = self.run_tool("audit.py", "--no-git")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("ERROR S10 demo.md: linked file does not exist: missing-topic.md", run.stdout)

    def test_relative_markdown_link_resolves_against_explicit_root(self):
        wl.write_text(self.page, PAGE.replace("- **Next:** nothing", "- **Next:** [evidence](../../docs/proof.md:7)"))
        os.mkdir(os.path.join(self.root, "docs"))
        wl.write_text(os.path.join(self.root, "docs", "proof.md"), "# Evidence\n")
        with tempfile.TemporaryDirectory() as other_root:
            run = self.run_tool("audit.py", "--root", self.root, "--no-git", WAYMARK_ROOT=other_root)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_status_links_can_use_the_repository_checkout_of_a_scratch_wiki(self):
        wl.write_text(self.page, PAGE.replace(
            "- **Next:** nothing",
            "- **Next:** [local topic](other.md), [evidence](../../docs/proof.md:7), and `src/proof.txt:3`"))
        with tempfile.TemporaryDirectory() as git_root:
            os.mkdir(os.path.join(git_root, "docs"))
            os.mkdir(os.path.join(git_root, "src"))
            wl.write_text(os.path.join(git_root, "docs", "proof.md"), "# Evidence\n")
            wl.write_text(os.path.join(git_root, "src", "proof.txt"), "review evidence\n")
            run = self.run_tool("audit.py", "--root", self.root, "--no-git", WAYMARK_GIT_ROOT=git_root)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

            os.unlink(os.path.join(git_root, "docs", "proof.md"))
            os.unlink(os.path.join(git_root, "src", "proof.txt"))
            # The selected repository is authoritative even if a scratch copy has these paths.
            os.mkdir(os.path.join(self.root, "docs"))
            os.mkdir(os.path.join(self.root, "src"))
            wl.write_text(os.path.join(self.root, "docs", "proof.md"), "# Scratch copy\n")
            wl.write_text(os.path.join(self.root, "src", "proof.txt"), "review evidence\n")
            run = self.run_tool("audit.py", "--root", self.root, "--no-git", WAYMARK_GIT_ROOT=git_root)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertRegex(run.stdout, r"WARN  W6 demo[.]md: [^\n]*[.][.]/[.][.]/docs/proof[.]md:7")
            self.assertRegex(run.stdout, r"WARN  W6 demo[.]md: [^\n]*src/proof[.]txt:3")
            strict = self.run_tool("audit.py", "--root", self.root, "--no-git", "--strict", WAYMARK_GIT_ROOT=git_root)
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)


class AuditInput(StatusBlockFixture):
    @unittest.skipUnless(sys.version_info >= (3, 11), "PYTHONSAFEPATH requires Python 3.11")
    def test_cli_imports_sibling_modules_in_safe_path_mode(self):
        run = self.run_tool("audit.py", "--no-git", PYTHONSAFEPATH="1")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_missing_sibling_import_exits_2(self):
        script = Path(self.root) / "audit.py"
        shutil.copyfile(SCRIPTS / script.name, script)
        run = self.run_tool(script, "--no-git")
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("ERROR IMPORT", run.stderr)

    def test_invalid_utf8_warns_once_per_file_and_audit_continues(self):
        with open(self.page, "ab") as handle:
            handle.write(b"\n[Corrupt history link](bad\xff.md)\n")
        with open(self.index, "ab") as handle:
            handle.write(b"\nCorrupt index text: \xff\xfe\n")
        with open(self.mem / "other.md", "a", encoding="utf-8") as handle:
            handle.write("\nValid replacement character: \ufffd\n")

        run = self.run_tool("audit.py", "--no-git")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        warnings = [line.split()[2] for line in run.stdout.splitlines() if line.startswith("  WARN  UTF8")]
        self.assertCountEqual(warnings, ["demo.md:", "index-a.md:"])
        self.assertIn("WARN  G1 demo.md: broken relative link: bad\ufffd.md", run.stdout)
        self.assertIn("STATUS PAGES 1", run.stdout)
        strict = self.run_tool("audit.py", "--no-git", "--strict")
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)


class FormatChecksWithoutGit(StatusBlockFixture):
    def audit_page(self, old, new):
        wl.write_text(self.page, wl.read(self.page).replace(old, new))
        self.assertEqual(self.run_tool("status.py", "sync").returncode, 0)
        return self.run_tool("audit.py", "--no-git")

    def test_tested_needs_dated_child_evidence(self):
        audit = self.audit_page("- **Tested:** none", "- **Tested:** passed")
        self.assertEqual(audit.returncode, 1, audit.stdout)
        self.assertIn("ERROR S8 demo.md: Tested: passed needs at least one dated child line", audit.stdout)

    def test_tested_children_need_date_and_revision(self):
        audit = self.audit_page("- **Tested:** none", "- **Tested:** passed\n  - unit tests green\n"
                                "  - 2026-01-02 @ `1abc234` — stale after the rebase")
        self.assertEqual(audit.returncode, 1, audit.stdout)
        self.assertIn("ERROR S8 demo.md: Tested line must start with a date", audit.stdout)
        self.assertIn("ERROR S8 demo.md: Tested line lacks an `@ <revision or label>`", audit.stdout)
        self.assertIn("WARN  W2 demo.md:", audit.stdout)
        self.assertNotIn("does not exist", audit.stdout)   # SHA and tag lookups stay git-only

    def test_published_tag_needs_date_and_evidence(self):
        audit = self.audit_page("- **Published:** no", "- **Published:** `v9.9.9`")
        self.assertEqual(audit.returncode, 1, audit.stdout)
        self.assertIn("ERROR S7 demo.md: Published 'v9.9.9' needs a date", audit.stdout)
        self.assertIn("ERROR S7 demo.md: Published 'v9.9.9' needs an evidence pointer", audit.stdout)
        self.assertNotIn("does not exist in git", audit.stdout)

    def test_merged_and_canonical_copy_need_backticked_refs(self):
        wl.write_text(self.page, wl.read(self.page).replace("- **Merged:** no — not in `origin/main`",
                                                            "- **Merged:** no — not yet")
                      .replace("- **Superseded:** none", "- **Superseded:** none\n"
                               "- **Canonical copy:** the other branch"))
        audit = self.run_tool("audit.py", "--no-git")
        self.assertEqual(audit.returncode, 1, audit.stdout)
        self.assertIn("ERROR S6 demo.md: Merged: no must name the target ref(s) in backticks", audit.stdout)
        self.assertIn("ERROR S9 demo.md: Canonical copy must name `<ref>:<path>` in backticks", audit.stdout)

