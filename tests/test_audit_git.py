from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import DATE, PAGE, WIKI_DIR, git, make_git_repo, run_tool, write_config


@unittest.skipUnless(shutil.which("git"), "Git is required for audit mutations")
class AuditMutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        checkout = tempfile.TemporaryDirectory()
        cls.addClassCleanup(checkout.cleanup)
        cls.git_root = checkout.name
        cls.base, cls.release, cls.feature = make_git_repo(cls.git_root)

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.mem = self.root / WIKI_DIR
        shutil.copytree(Path(self.git_root) / WIKI_DIR, self.mem)
        self.page = self.mem / "demo.md"
        self.index = self.mem / "index-a.md"
        page = PAGE.replace(
            "checked at `1abc234` (`main`)", f"checked at `{self.feature}` (`feature-x`)"
        ).replace(
            "committed — `1abc234`", f"committed — `{self.feature}`"
        ).replace(
            "no — not in `origin/main`", "integration — in `feature-x`, not in `origin/main`"
        ).replace(
            "- **Tested:** none",
            f"- **Tested:** partial\n  - {DATE} review @ `{self.feature}`: pass",
        ).replace(
            "- **Published:** no",
            f"- **Published:** `v1.0.0` → `{self.release}` ({DATE}, evidence:7)",
        ).replace(
            "- **Next:** nothing", "- **Next:** review evidence"
        )
        self.page.write_text(page, encoding="utf-8")
        result = self.run_tool("status.py", "sync")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_tool(self, script, *args, **overrides):
        return run_tool(
            script, *args, cwd=self.root, WAYMARK_ROOT=self.root,
            WAYMARK_GIT_ROOT=self.git_root, **overrides,
        )

    def mutate(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def expect(self, code, result=None):
        if result is None:
            result = self.run_tool("audit.py")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(f"ERROR {code} demo.md:", output)
        return output

    def test_clean_copy_passes(self):
        self.assertEqual(git(self.git_root, "status", "--porcelain"), "")
        self.assertEqual(git(self.git_root, "rev-parse", "HEAD^"), self.base)
        self.assertEqual(git(self.git_root, "rev-parse", "origin/main"), self.release)
        self.assertEqual(git(self.git_root, "rev-parse", "feature-x^"), self.release)
        self.assertEqual(git(self.git_root, "cat-file", "-t", "v1.0.0"), "tag")
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)

    def test_index_drift(self):
        self.mutate(self.index, "merged integration", "merged no")
        self.expect("S4")
        check = self.run_tool("status.py", "check")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("index", check.stdout + check.stderr)

    def test_fake_sha_backticked_and_bare(self):
        for bad_sha in ("`f00d1234abc`", "f00d1234abc"):
            with self.subTest(bad_sha=bad_sha):
                original = self.page.read_bytes()
                try:
                    self.mutate(self.page, "- **Next:** review evidence",
                                f"- **Next:** see {bad_sha}, then review evidence")
                    self.assertIn("f00d1234abc", self.expect("S5"))
                finally:
                    self.page.write_bytes(original)

    def test_unknown_checked_at_sha_is_reported_once(self):
        bad_sha = "f00d1234abc"
        other_bad_sha = "bad1234f00d"
        self.mutate(self.page, f"checked at `{self.feature}`", f"checked at `{bad_sha}`")
        self.mutate(self.page, "- **Next:** review evidence",
                    f"- **Next:** compare `{bad_sha}` and `{other_bad_sha}`")
        output = self.expect("S2")
        self.assertEqual(output.count(bad_sha), 1, output)
        self.assertIn(f"ERROR S5 demo.md: SHA `{other_bad_sha}`", output)

    def test_merged_main_checked_against_origin_main(self):
        self.mutate(self.page, "integration — in `feature-x`, not in `origin/main`",
                    "main — in `feature-x`")
        self.assertIn("origin/main", self.expect("S6"))

    def test_missing_tag(self):
        self.mutate(self.page, "- **Published:** `v1.0.0`", "- **Published:** `v9.9.9`")
        self.assertIn("v9.9.9", self.expect("S7"))

    def test_missing_path_in_a_commit_is_still_an_error(self):
        self.mutate(self.page, "- **Next:** review evidence",
                    "- **Next:** review evidence in `HEAD:missing/path`")
        self.assertIn("HEAD:missing/path", self.expect("S10"))

    def test_slash_tokens_only_report_plausible_repository_paths(self):
        self.mutate(self.page, "- **Next:** review evidence",
                    "- **Next:** `localhost:3000/health`, `git@github.com:org/repo.git`, "
                    "`https://example.invalid/sample.py`, `application/json`, `and/or`, `n/a`, "
                    "`docs/gone.py`, `missing/gone.py`, and `docs/gone/`")
        audit = self.run_tool("audit.py")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        warnings = [line.rsplit(": ", 1)[1] for line in audit.stdout.splitlines() if line.startswith("  WARN  W6")]
        self.assertCountEqual(warnings, ["docs/gone.py", "missing/gone.py", "docs/gone/"])

    def test_ref_paths_are_relative_to_project_subdirectory(self):
        self.mutate(self.page, "- **Next:** review evidence",
                    "- **Next:** review `HEAD:evidence/README`\n"
                    "- **Canonical copy:** `feature-x:evidence/README`")
        audit = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root,
                         WAYMARK_GIT_ROOT=Path(self.git_root) / "docs")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertNotIn("SKIPPED git checks", audit.stdout)

        self.mutate(self.page, "`HEAD:evidence/README`", "`HEAD:docs/evidence/README` and `HEAD:evidence/README/`")
        audit = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root,
                         WAYMARK_GIT_ROOT=Path(self.git_root) / "docs")
        output = self.expect("S10", audit)
        self.assertIn("path does not exist in ref: HEAD:docs/evidence/README", output)
        self.assertIn("path does not exist in ref: HEAD:evidence/README/", output)

    def test_missing_field(self):
        self.mutate(self.page, "- **Next:**", "- **Nxt:**")
        self.assertIn("Next", self.expect("S1"))

    def test_tested_without_at(self):
        self.mutate(self.page, f" @ `{self.feature}`: pass", ": pass")
        self.expect("S8")

    def test_void_tested_line_warns_not_errors(self):
        self.mutate(self.page, ": pass", ": void for the feature tip")
        audit = self.run_tool("audit.py")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertIn("WARN  W2 demo.md:", audit.stdout)
        strict = self.run_tool("audit.py", "--strict")
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_custom_main_ref_controls_merged_claim(self):
        self.mutate(self.page, "integration — in `feature-x`, not in `origin/main`",
                    "main — in `feature/nested`")
        sync = self.run_tool("status.py", "sync")
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.expect("S6")
        write_config(self.root, main_ref="feature/nested")
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)

    def test_merged_label_claim_checked_against_main_ref(self):
        write_config(self.root, main_ref="feature/nested", merged_label="staging")
        self.mutate(self.page, "integration — in `feature-x`, not in `origin/main`",
                    "staging — in `feature/nested`")
        sync = self.run_tool("status.py", "sync")
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertIn("merged staging", self.index.read_text(encoding="utf-8"))
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        write_config(self.root, main_ref="origin/main", merged_label="staging")
        output = self.expect("S6")
        self.assertIn("Merged: staging but Code commit %s is not an ancestor of `origin/main`"
                      % self.feature, output)

    def test_merged_label_claim_needs_ref_and_code_sha(self):
        write_config(self.root, main_ref="feature/nested", merged_label="staging")
        self.mutate(self.page, "integration — in `feature-x`, not in `origin/main`", "staging — merged")
        self.mutate(self.page, f"committed — `{self.feature}`", "committed — see history")
        self.assertEqual(self.run_tool("status.py", "sync").returncode, 0)
        output = self.expect("S6")
        self.assertIn("Merged: staging must name the target ref(s) in backticks", output)
        self.assertIn("Merged: staging needs at least one backticked SHA in Code", output)

    def test_release_tag_pattern_is_opt_in(self):
        self.mutate(self.page, "old body", "See train-42, train-42, and v4.0.0.")
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        write_config(self.root, release_tag_pattern=r"\btrain-\d+\b")
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 1, audit.stdout + audit.stderr)
        self.assertIn("WARN  G2 demo.md:", audit.stdout)
        self.assertEqual(audit.stdout.count("train-42"), 1)
        self.assertNotIn("v4.0.0", audit.stdout)

    def test_status_allows_slashed_ref_and_nested_paths(self):
        self.mutate(self.page, "in `feature-x`", "in `feature/nested`")
        self.mutate(
            self.page, "- **Next:** review evidence",
            "- **Next:** review `feature/nested:docs/evidence/README` and `docs/evidence/README:1`",
        )
        audit = self.run_tool("audit.py", "--strict")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
