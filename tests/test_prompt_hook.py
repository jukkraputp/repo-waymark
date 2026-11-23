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

    def test_issue_key_in_index_keywords_qualifies_in_hook(self):
        self.add_results(1, "Keywords: ABC-123, UTF-8 encoding")
        for prompt, matches in (("ABC-123", True), ("UTF-8 encoding", False)):
            with self.subTest(prompt=prompt):
                hook = run_tool("prompt_hook.py", cwd=self.root, input_text=json.dumps({"prompt": prompt}))
                self.assertEqual(hook.returncode, 0, hook.stderr)
                if matches:
                    self.assertTrue(hook.stdout.startswith("<wiki-index-matches>\n"))
                    self.assertIn("result-0.md", hook.stdout)
                else:
                    self.assertEqual(hook.stdout, "")

    def test_hook_ignores_issue_shaped_terms_only_in_page_bodies(self):
        self.add_results(1, "Protocol and model notes")
        (self.root / WIKI_DIR / "result-0.md").write_text(
            "# Result 0\n\nThe CDN serves http-2 by default.\n", encoding="utf-8",
        )
        for prompt in ("HTTP-2", "is GPT-5 better than HTTP-2 for this?"):
            with self.subTest(prompt=prompt):
                hook = run_tool("prompt_hook.py", cwd=self.root, input_text=json.dumps({"prompt": prompt}))
                self.assertEqual(hook.returncode, 0, hook.stderr)
                self.assertEqual(hook.stdout, "")


class HookSkipsSelfStartedTurns(unittest.TestCase):
    BODY = "UniqueWidget finished with overview notes"
    ENVELOPES = (
        '<task-notification>\n<task-id>t1</task-id>\n%s\n</task-notification>',
        '<agent-message from="worker">\n[Subagent hand-back] %s',
        '<side-sessions-event>%s</side-sessions-event>',
        '<ci-monitor-event>%s</ci-monitor-event>',
        '<teammate-message id="1">%s</teammate-message>',
        '<channel-message source="x">%s</channel-message>',
    )

    def setUp(self):
        self.root = Path(fixture())
        self.addCleanup(shutil.rmtree, self.root)
        mem = self.root / WIKI_DIR
        with (mem / "index-a.md").open("a", encoding="utf-8") as stream:
            stream.write("- [Widget notes](widget.md) — UniqueWidget overview notes\n")
        (mem / "widget.md").write_text("# Widget\n", encoding="utf-8")

    def hook(self, payload):
        run = run_tool("prompt_hook.py", cwd=self.root, input_text=json.dumps(payload))
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_control_prompt_matches(self):
        self.assertIn("widget.md", self.hook({"prompt": self.BODY}))

    def test_envelope_prompts_print_nothing(self):
        for template in self.ENVELOPES:
            for prefix in ("", "  \n\t"):
                with self.subTest(template=template[:20], prefix=repr(prefix)):
                    self.assertEqual(self.hook({"prompt": prefix + template % self.BODY}), "")

    def test_envelope_not_at_start_still_matches(self):
        for prompt in (
            "see " + self.ENVELOPES[0] % self.BODY,
            '<pasted_content id="1">' + self.ENVELOPES[0] % self.BODY + "</pasted_content>",
            "/review " + self.BODY,
            "<task-notificationX>" + self.BODY,
        ):
            with self.subTest(prompt=prompt[:30]):
                self.assertIn("widget.md", self.hook({"prompt": prompt}))

    def test_prompt_source_system_skips(self):
        self.assertEqual(self.hook({"prompt": self.BODY, "promptSource": "system"}), "")

    def test_origin_kind_non_user_skips(self):
        self.assertEqual(self.hook({"prompt": self.BODY, "origin": {"kind": "task-notification"}}), "")

    def test_user_sources_still_match(self):
        for extra in ({"promptSource": "user"}, {"origin": {"kind": "user"}}, {"origin": {}}):
            with self.subTest(extra=extra):
                self.assertIn("widget.md", self.hook({"prompt": self.BODY, **extra}))
