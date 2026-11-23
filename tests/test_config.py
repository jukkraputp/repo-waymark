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
