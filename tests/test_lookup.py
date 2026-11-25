import builtins
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from helpers import PAGE, WIKI_DIR, fixture, git, init_git, make_wiki, run_tool, sync_fixture, wl

import lookup


class IssueKeyLookup(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        make_wiki(self.root, {"indexed.md": "# Indexed\n", "body.md": "# Body\n\n(proj-12)\n"},
                  "- [Indexed](indexed.md) — Keywords: ABC-123\n- [Body](body.md)\n")

    def test_issue_key_in_index_keywords(self):
        run = run_tool("lookup.py", "ABC-123", cwd=self.root)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("identifiers=[ABC-123]", run.stdout)
        self.assertIn("indexed.md", run.stdout)

    def test_issue_key_only_in_page_body(self):
        run = run_tool("lookup.py", "PROJ-12", cwd=self.root)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Identifier matches in page bodies:", run.stdout)
        self.assertIn("PROJ-12 ×1  body.md:3", run.stdout)

        hook = run_tool("prompt_hook.py", "PROJ-12", cwd=self.root)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")


class IdentifierWordsAreNotKeywords(unittest.TestCase):
    """Words inside a query identifier must not match a longer, different identifier."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        make_wiki(self.root, {"longer.md": "# Longer\n\nTracked as PROJ-123.\n"},
                  "- [Longer key](longer.md) — Keywords: PROJ-123, cache eviction\n")

    def test_shorter_issue_key_does_not_match_longer_key(self):
        run = run_tool("lookup.py", "PROJ-12", cwd=self.root)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("keywords=[]", run.stdout)
        self.assertNotIn("longer.md", run.stdout)
        hook = run_tool("prompt_hook.py", "PROJ-12", cwd=self.root)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")

    def test_exact_issue_key_and_plain_words_still_match(self):
        for query in ("PROJ-123", "proj", "cache eviction"):
            with self.subTest(query=query):
                run = run_tool("lookup.py", query, cwd=self.root)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("longer.md", run.stdout)
        hook = run_tool("prompt_hook.py", "PROJ-123", cwd=self.root)
        self.assertIn("longer.md", hook.stdout)


class MapRoleLookup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.wiki = make_wiki(self.root, {
            "indexed.md": "# Requests\n\n" + self.map_text("`lib/send.py` — request timeout"),
            "priority.md": "# Priority\n\n" + self.map_text("`lib/priority.py` — job timeout"),
            "route.md": "# Route settings\n\n" + self.map_text(
                "`lib/socket_backoff.py` `HTTPBroker.configureLatency` — timeout for requests"),
            "prose.md": "# Notes\n\nTimeout, latency, broker, backoff.\n",
        }, "- [Requests](indexed.md) — Keywords: routing\n- [Route settings](route.md)\n")

    @staticmethod
    def map_text(bullet):
        return "<!-- wiki-map v1 -->\n## Where to look\n\n- " + bullet + "\n"

    def lookup(self, *args):
        return run_tool("lookup.py", *args, cwd=self.root)

    def test_map_words_are_ranked_after_index_hits(self):
        query = "routing timeouts latenc broker configure backoff"
        run = self.lookup("--json", query)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        hits = json.loads(run.stdout)["hits"]
        self.assertEqual([Path(hit["page"]).name for hit in hits],
                         ["indexed.md", "route.md", "priority.md"])
        self.assertEqual(hits[1]["category"], "map match")
        self.assertEqual(hits[1]["matched"], ["backoff", "broker", "configure", "latenc", "timeouts"])
        self.assertEqual(hits[1]["map"][0]["symbol"], "HTTPBroker.configureLatency")

    def test_map_only_matches_succeed_in_text(self):
        run = self.lookup("timeout")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("(map match; matched: timeout)", run.stdout)
        self.assertIn("route.md", run.stdout)
        self.assertNotIn("prose.md", run.stdout)

    def test_deep_shows_map_matches_alongside_a_full_index_list(self):
        normal = self.lookup("--max", "1", "routing latency")
        self.assertNotIn("route.md", normal.stdout)
        deep = self.lookup("--max", "1", "--deep", "routing latency")
        self.assertIn("route.md  (map match; matched: latency)", deep.stdout)

    def test_masked_identifiers_are_not_map_keywords(self):
        run = self.lookup("`timeout`")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertNotIn("map match", run.stdout)

    def test_equivalent_query_forms_match_plural_map_words_once(self):
        (self.wiki / "priority.md").write_text(
            "# Priority\n\n" + self.map_text("`lib/priority.py` — queue entries"), encoding="utf-8")
        run = self.lookup("--json", "entry entries")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["hits"][0]["matched"], ["entry"])

    def test_map_search_stops_after_read_budget(self):
        (self.wiki / "a-padding.md").write_bytes(b"x" * 1_999_990)
        (self.wiki / "b-partial.md").write_text(
            "# Partial\n\n" + self.map_text("`lib/timeout.py` — timeout"), encoding="utf-8")
        bytes_read = []

        class CountingReader:
            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                self.fh.__enter__()
                return self

            def __exit__(self, *args):
                return self.fh.__exit__(*args)

            def read(self, *args):
                data = self.fh.read(*args)
                bytes_read.append(len(data))
                return data

            def fileno(self):
                return self.fh.fileno()

        def counting_open(*args, **kwargs):
            return CountingReader(builtins.open(*args, **kwargs))

        with patch.object(lookup, "open", counting_open, create=True):
            matches = lookup.map_matches(self.root, wl.config_for(self.root),
                                         {"timeout": {"timeout"}}, set(), 5)
        self.assertLessEqual(sum(bytes_read), 2_000_000)
        self.assertEqual(sum(bytes_read), 2_000_000)
        self.assertNotIn("b-partial.md", [match[0] for match in matches])



class MapRanking(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def lookup(self, maps, *args, index=""):
        make_wiki(self.root, {
            name: "# Topic\n\n<!-- wiki-map v1 -->\n## Where to look\n\n"
                  + "".join("- " + bullet + "\n" for bullet in bullets)
            for name, bullets in maps.items()
        }, index)
        run = run_tool("lookup.py", "--json", *args, cwd=self.root)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)["hits"]

    def test_rare_single_term_outranks_common_map_term(self):
        maps = {
            "a.md": ["`lib/send.py` — client calls", "`lib/client.py` — client options"],
            "b.md": ["`lib/retry.py` `RetryClient` — request retries"],
            "c.md": ["`lib/client_config.py` — request options"],
            "z.md": ["`lib/wait.py` — request timeout"],
        }
        hits = self.lookup(maps, "--max", "3", "client timeout")
        self.assertEqual([Path(hit["page"]).name for hit in hits], ["z.md", "a.md", "b.md"])
        self.assertEqual([hit["score"] for hit in hits], [1, 0.333, 0.333])
        self.assertEqual([hit["matched"] for hit in hits], [["timeout"], ["client"], ["client"]])
        hits = self.lookup(maps, "timeout")
        self.assertEqual([Path(hit["page"]).name for hit in hits], ["z.md"])

    def test_trap_role_symbol_and_basename_supply_no_map_keywords(self):
        maps = {
            "a.md": ["Trap: `lib/backend_transport.py` `BackendSocket` — backend timeout"],
            "z.md": ["`lib/wait.py` — request timeout",
                     "Trap: `lib/backend_transport.py` `BackendSocket` — backend timeout"],
        }
        top = self.lookup(maps, "--max", "1", "backend timeout")
        self.assertEqual([Path(hit["page"]).name for hit in top], ["z.md"])
        hits = self.lookup(maps, "backend timeout transport socket")
        self.assertEqual([Path(hit["page"]).name for hit in hits], ["z.md"])
        self.assertEqual(hits[0]["matched"], ["timeout"])
        self.assertEqual(hits[0]["score"], 1)
        self.assertTrue(hits[0]["map"][1]["trap"])

    def test_deep_uses_map_for_an_index_hit_below_the_display_limit(self):
        hits = self.lookup({
            "a.md": ["`lib/relay.py` — routing timeout backend"],
            "b.md": ["`lib/wait.py` — backend timeout"],
        }, "--max", "1", "--deep", "routing timeout backend",
            index="- [Relay](a.md) — Keywords: routing backend\n- [Wait](b.md) — Keywords: routing\n")
        self.assertEqual([Path(hit["page"]).name for hit in hits], ["a.md", "b.md"])
        self.assertEqual(hits[1]["category"], "map match")
        self.assertEqual(hits[1]["matched"], ["backend", "timeout"])
        self.assertEqual(hits[1]["score"], 2)


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
