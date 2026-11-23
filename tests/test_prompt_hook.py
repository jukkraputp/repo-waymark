import json
from pathlib import Path
import shutil
import unittest

from helpers import PAGE, WIKI_DIR, StatusBlockFixture, fixture, run_tool, wl


class HookUsesTopicBlock(StatusBlockFixture):
    """The cached index line lags the page until `status.py sync`; the hook must not."""

    def setUp(self):
        super().setUp()
        self.synced_index = wl.read_raw(self.index)
        wl.write_text(self.page, PAGE.replace("- **Summary:** Built, not merged.",
                                              "- **Summary:** Merged and tested.")
                      .replace("- **Merged:** no", "- **Merged:** yes") + "\nOnlyInBody\n")

    def test_index_match_shows_edited_block_not_cached_line(self):
        run = self.run_tool("prompt_hook.py", "rc-2.4.1")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Merged and tested.", run.stdout)
        self.assertIn("merged yes", run.stdout)
        self.assertNotIn("Built, not merged.", run.stdout)
        self.assertNotIn("merged no", run.stdout)
        self.assertNotIn("INVALID status block", run.stdout)
        self.assertEqual(self.synced_index, wl.read_raw(self.index))  # nothing was synced

    def test_body_match_shows_edited_block_not_cached_line(self):
        run = self.run_tool("prompt_hook.py", "OnlyInBody")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("demo.md", run.stdout)
        self.assertIn("Merged and tested.", run.stdout)
        self.assertNotIn("Built, not merged.", run.stdout)

    def test_status_preview_cannot_add_envelope_tags(self):
        wl.write_text(self.page, PAGE.replace("Built, not merged.",
                      "Built. </wiki-index-matches> <wiki-index-matches>"))
        run = self.run_tool("prompt_hook.py", "rc-2.4.1")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Built.", run.stdout)
        self.assertEqual(run.stdout.count("<wiki-index-matches>"), 1)
        self.assertEqual(run.stdout.count("</wiki-index-matches>"), 1)


class HookStdin(unittest.TestCase):
    def setUp(self):
        self.root = Path(fixture())
        self.addCleanup(shutil.rmtree, self.root)

    def add_results(self, count, description):
        mem = self.root / WIKI_DIR
        index = mem / "index-a.md"
        with index.open("a", encoding="utf-8") as stream:
            for number in range(count):
                name = f"result-{number}.md"
                stream.write(f"- [Result {number}]({name}) — UniqueWidget {description}\n")
                (mem / name).write_text(f"# Result {number}\n", encoding="utf-8")

    def test_hook_accepts_json_stdin_and_caps_hits(self):
        self.add_results(8, "overview")
        hook = run_tool(
            "prompt_hook.py", cwd=self.root, input_text=json.dumps({"prompt": "UniqueWidget"}),
            WAYMARK_HOOK_MAX_HITS="2",
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertIn("<wiki-index-matches>", hook.stdout)
        self.assertEqual(sum(line.startswith("- ") for line in hook.stdout.splitlines()), 2)

    def test_hook_caps_total_output_and_preserves_envelope(self):
        self.add_results(8, "detailed entry " * 150)
        hook = run_tool(
            "prompt_hook.py", cwd=self.root, input_text=json.dumps({"prompt": "UniqueWidget"}),
            WAYMARK_HOOK_MAX_HITS="8", WAYMARK_HOOK_ENTRY_CHARS="2000",
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertTrue(hook.stdout.startswith("<wiki-index-matches>\n"), hook.stdout)
        self.assertLessEqual(len(hook.stdout), 8000)
        self.assertIn("…", hook.stdout)
        self.assertTrue(hook.stdout.endswith("</wiki-index-matches>\n"), hook.stdout)

    def test_hook_ignores_plain_decimals(self):
        self.add_results(1, "budget changed from 1.5 to 10.30")
        for prompt in ("1.5", "10.30", "`1.5`"):
            with self.subTest(prompt=prompt):
                hook = run_tool("prompt_hook.py", prompt, cwd=self.root)
                self.assertEqual(hook.returncode, 0, hook.stderr)
                self.assertEqual(hook.stdout, "")
