import json
from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import DATE, PAGE, WIKI_DIR, fixture, make_wiki, run_tool, wl, write_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(fixture())
        self.addCleanup(shutil.rmtree, self.root)

    def run_tool(self, script, *args, cwd=None, **kwargs):
        return run_tool(script, *args, cwd=cwd or self.root, **kwargs)

    def test_defaults_without_config(self):
        config = wl.config_for(self.root)
        self.assertEqual(Path(wl.wiki_dir(self.root, config)), self.root / WIKI_DIR)
        self.assertEqual(config["main_ref"], "origin/main")
        self.assertIsNone(config["release_tag_pattern"])
        self.assertFalse(config["extra_fields"])
        sync = self.run_tool("status.py", "sync")
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        result = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["hits"][0]["page"], ".claude/memory/demo.md")

    def test_custom_wiki_directory_in_all_tools(self):
        default_wiki = self.root / WIKI_DIR
        make_wiki(
            self.root,
            {
                name: (default_wiki / name).read_text(encoding="utf-8")
                for name in ("demo.md", "other.md")
            },
            (default_wiki / "index-a.md").read_text(encoding="utf-8"),
            config={"wiki_dir": "handbook/current"},
        )
        for script, args in (
            ("status.py", ("sync",)), ("status.py", ("check",)), ("audit.py", ("--no-git",))
        ):
            with self.subTest(script=script, args=args):
                result = self.run_tool(script, *args, "--root", self.root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lookup = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        self.assertEqual(lookup.returncode, 0, lookup.stdout + lookup.stderr)
        self.assertEqual(json.loads(lookup.stdout)["hits"][0]["page"], "handbook/current/demo.md")
        hook = self.run_tool("prompt_hook.py", "--root", self.root, "rc-2.4.1")
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        self.assertIn("handbook/current", hook.stdout)
        self.assertIn("demo.md", hook.stdout)

    def test_walk_up_from_nested_directory(self):
        work = self.root / "work" / "subdirectory"
        work.mkdir(parents=True)
        normal = self.run_tool("lookup.py", "DemoThing", cwd=work)
        self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
        self.assertIn("Other", normal.stdout)
        with tempfile.TemporaryDirectory() as unrelated:
            from_project_dir = self.run_tool(
                "lookup.py", "DemoThing", cwd=unrelated, CLAUDE_PROJECT_DIR=work,
            )
        self.assertEqual(from_project_dir.returncode, 0, from_project_dir.stdout + from_project_dir.stderr)
        self.assertIn("Other", from_project_dir.stdout)

    def test_missing_wiki_and_git_boundary(self):
        work = self.root / "nested-project"
        work.mkdir()
        (work / ".git").mkdir()
        for script, flags in (
            ("lookup.py", ("DemoThing",)), ("status.py", ("check",)), ("audit.py", ("--no-git",)),
        ):
            with self.subTest(script=script):
                result = self.run_tool(script, *flags, cwd=work)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("wiki", (result.stdout + result.stderr).lower())
                self.assertNotIn("Traceback", result.stderr)
        hook = self.run_tool("prompt_hook.py", "DemoThing", cwd=work)
        self.assertEqual(hook.returncode, 0)
        self.assertEqual(hook.stdout + hook.stderr, "")

    def test_extra_fields_reproduce_legacy_rendering_byte_for_byte(self):
        write_config(
            self.root, extra_fields={
                "Deploy env": {
                    "vocab": ["staging", "production", "n/a", "unknown"],
                    "label": "deploy",
                    "omit_when": "n/a",
                },
            }
        )
        page = self.root / WIKI_DIR / "demo.md"
        index = self.root / WIKI_DIR / "index-a.md"
        for value, head in (("n/a", ""), ("staging — evidence", " · deploy staging")):
            with self.subTest(value=value):
                page.write_text(
                    PAGE.replace("- **Tested:**", f"- **Deploy env:** {value}\n- **Tested:**"),
                    encoding="utf-8",
                )
                result = self.run_tool("status.py", "sync")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = (
                    "# idx\n- [Demo topic](demo.md) — "
                    f"**Status {DATE}:** decision approved · code committed · merged no"
                    f"{head} · tested none · published no — Built, not merged. "
                    "· About: A demo. · Keywords: demo, DemoThing, rc-2.4.1\n"
                    "- [Other](other.md) — plain words ready launch rc-2.4.10 DemoThing note\n"
                )
                self.assertEqual(index.read_bytes(), expected.encode("utf-8"))
                before = page.read_bytes() + index.read_bytes()
                rerun = self.run_tool("status.py", "sync")
                self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
                self.assertEqual(page.read_bytes() + index.read_bytes(), before)


class MergedLabelTests(unittest.TestCase):
    """`merged_label` renames the Merged value that means "in `main_ref`"."""

    def setUp(self):
        self.root = Path(fixture())
        self.addCleanup(shutil.rmtree, self.root)
        self.page = self.root / WIKI_DIR / "demo.md"
        self.index = self.root / WIKI_DIR / "index-a.md"

    def run_tool(self, script, *args):
        return run_tool(script, *args, cwd=self.root, WAYMARK_ROOT=self.root)

    def write_page(self, merged):
        self.page.write_text(
            PAGE.replace("- **Merged:** no — not in `origin/main`", f"- **Merged:** {merged}"),
            encoding="utf-8",
        )

    def test_default_label_is_main(self):
        config = wl.config_for(self.root)
        self.assertEqual(config["merged_label"], "main")
        self.assertEqual(wl.merged_vocab(config), wl.VOCAB["Merged"])
        self.assertEqual(wl.status_template(config), wl.TEMPLATE)

    def test_configured_label_renders_in_index_hook_and_lookup(self):
        write_config(self.root, main_ref="origin/staging", merged_label="staging")
        self.write_page("staging — in `origin/staging`, not in `origin/main`")
        sync = self.run_tool("status.py", "sync")
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertIn("· merged staging ·", self.index.read_text(encoding="utf-8"))
        check = self.run_tool("status.py", "check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        audit = self.run_tool("audit.py", "--no-git")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        hook = self.run_tool("prompt_hook.py", "rc-2.4.1")
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        self.assertIn("merged staging", hook.stdout)
        self.assertNotIn("merged main", hook.stdout)
        lookup = self.run_tool("lookup.py", "rc-2.4.1")
        self.assertEqual(lookup.returncode, 0, lookup.stdout + lookup.stderr)
        self.assertIn("Merged: staging", lookup.stdout)

    def test_default_main_is_rejected_when_a_label_is_configured(self):
        write_config(self.root, main_ref="origin/staging", merged_label="staging")
        self.write_page("main — in `origin/staging`")
        check = self.run_tool("status.py", "check")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("write `Merged: staging`", check.stdout)
        self.assertNotIn("no valid status block", check.stdout)
        sync = self.run_tool("status.py", "sync")
        self.assertEqual(sync.returncode, 1, sync.stdout + sync.stderr)
        self.assertNotIn("merged main", self.index.read_text(encoding="utf-8"))
        audit = self.run_tool("audit.py", "--no-git")
        self.assertEqual(audit.returncode, 1, audit.stdout + audit.stderr)
        self.assertIn("ERROR S3 demo.md:", audit.stdout)
        self.assertIn("write `Merged: staging`", audit.stdout)
        hook = self.run_tool("prompt_hook.py", "rc-2.4.1")
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        self.assertIn("write `Merged: staging`", hook.stdout)
        self.assertNotIn("merged main", hook.stdout)

    def test_template_shows_configured_label(self):
        write_config(self.root, merged_label="staging")
        result = self.run_tool("status.py", "template")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("- **Merged:** <no|integration|staging|partial|n/a|unknown>", result.stdout)
        self.assertNotIn("|main|", result.stdout)

    def test_invalid_labels_are_config_errors(self):
        for label in ("No", "integration", "has space", "partial", "n/a", "", "staging.", 7, None,
                      "x" * 33):
            with self.subTest(label=label):
                write_config(self.root, merged_label=label)
                with self.assertRaises(wl.ConfigError):
                    wl.config_for(self.root)
                for script, args in (("status.py", ("check",)), ("audit.py", ("--no-git",)),
                                     ("lookup.py", ("rc-2.4.1",))):
                    result = self.run_tool(script, *args)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("merged_label", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                hook = self.run_tool("prompt_hook.py", "rc-2.4.1")
                self.assertEqual((hook.returncode, hook.stdout), (0, ""))

    def test_valid_labels_are_accepted(self):
        for label in ("staging", "dev", "release/next", "rc-1.2", "main"):
            with self.subTest(label=label):
                write_config(self.root, merged_label=label)
                self.assertEqual(wl.config_for(self.root)["merged_label"], label)
