from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import PAGE, WIKI_DIR, fixture, git, init_git, run_tool, sync_fixture, wl


@unittest.skipUnless(shutil.which("git"), "Git is required for branch lookup")
class BranchLookup(unittest.TestCase):
    ref = "topic-branch"
    path = (WIKI_DIR / "demo.md").as_posix()

    def setUp(self):
        self.root = fixture()
        self.addCleanup(shutil.rmtree, self.root)
        self.page = Path(self.root) / WIKI_DIR / "demo.md"
        wl.write_text(self.page, PAGE.replace(
            "\n<!-- history -->", f"\n- **Canonical copy:** `{self.ref}:{self.path}`\n\n<!-- history -->"))
        sync_fixture(self.root)

    def lookup(self, branch, *args):
        with tempfile.TemporaryDirectory() as git_root:
            init_git(git_root)
            branch_page = Path(git_root) / self.path
            branch_page.parent.mkdir(parents=True)
            branch_page.write_text(branch, encoding="utf-8")
            git(git_root, "add", self.path)
            git(git_root, "commit", "--quiet", "-m", "Record branch evidence")
            self.tip = git(git_root, "rev-parse", "HEAD")
            git(git_root, "branch", self.ref)
            completed = run_tool(
                "lookup.py", "--branches", *args, "rc-2.4.1",
                cwd=self.root, WAYMARK_ROOT=self.root, WAYMARK_GIT_ROOT=git_root,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            return completed.stdout

    def test_legacy_branch_shows_only_latest_dated_heading_as_context(self):
        branch = ("# Demo topic 2024-06-18\n\n"
                  "## Status 2024-06-18 — Review steps pending\nOld contradictory claim.\n\n"
                  "## Review session 2024-06-19 — saved\nUnverified context body.\n\n## Links\n")
        output = self.lookup(branch)
        self.assertIn(self.ref, output)
        self.assertIn(f"git show {self.tip}:{self.path}", output)
        self.assertIn(f"branch page has no Current status block; read the record at {self.tip}:{self.path}", output)
        self.assertIn("context", output.lower())
        self.assertIn("Review session 2024-06-19 — saved", output)
        self.assertNotIn("Review steps pending", output)
        self.assertNotIn("Unverified context body.", output)

    def test_branch_with_current_status_shows_the_block(self):
        branch = PAGE.replace("Built, not merged.", "Branch summary.").replace(
            "old body", "## Status 2024-06-19\nOlder branch claim.")
        output = self.lookup(branch)
        self.assertIn(f"git show {self.tip}:{self.path}", output)
        self.assertIn("Branch summary.", output)
        self.assertNotIn("Older branch claim.", output)
        self.assertIn("Branch summary.", self.lookup(branch, "--json"))

    def test_branch_can_name_current_status_without_opt_in_marker(self):
        branch = PAGE.replace(wl.MARKER + "\n", "").replace("Built, not merged.", "Branch summary.")
        output = self.lookup(branch)
        self.assertIn("Branch summary.", output)
        self.assertNotIn("branch page has no Current status block", output)

    def test_branch_with_duplicate_fields_reports_invalid_status(self):
        branch = PAGE.replace("- **Code:** committed", "- **Code:** uncommitted\n- **Code:** committed")
        branch = branch.replace("Built, not merged.", "Withhold branch summary.")
        output = self.lookup(branch)
        self.assertIn("INVALID status block: duplicate field Code", output)
        self.assertNotIn("Withhold branch summary.", output)

    def test_branch_path_is_relative_to_waymark_root_inside_checkout(self):
        root = Path(self.root)
        project = root / "project"
        project.mkdir()
        (root / ".claude").rename(project / ".claude")
        init_git(root)
        git(root, "add", ".")
        git(root, "commit", "--quiet", "-m", "Record nested project")
        git(root, "branch", self.ref)
        tip = git(root, "rev-parse", "HEAD")
        completed = run_tool("lookup.py", "--root", project, "--branches", "rc-2.4.1", cwd=root)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn(f"git show {tip}:project/{self.path}", completed.stdout)
        self.assertNotIn("branch page could not be read", completed.stdout)
