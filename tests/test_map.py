"""Parsing and audit checks for the optional "Where to look" map."""
from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import PAGE, WIKI_DIR, git, init_git, make_wiki, run_tool, write_config
import map as maptool
import maplib
import wikilib as wl

maplib_free_parse = maptool.parse_numstat

MAP = """<!-- wiki-map v1 -->
## Where to look

- `lib/app.py` `App.start` — entry point
- Trap: `lib/old.py` — legacy; use `lib/app.py`
- `feat:lib/new.py` — unmerged, on its branch only
"""


def codes(section, g=None, ref="main"):
    return [c for c, _ in maplib.audit_section(section, g, ref)]


class ParseMap(unittest.TestCase):
    def test_no_marker_is_none(self):
        self.assertIsNone(maplib.parse_map("# T\n\ntext\n"))

    def test_parses_entries(self):
        s = maplib.parse_map("# T\n\n" + MAP)
        self.assertTrue(s.usable, s.issues)
        e = s.entries
        self.assertEqual([x.path for x in e], ["lib/app.py", "lib/old.py", "lib/new.py"])
        self.assertEqual(e[0].symbol, "App.start")
        self.assertEqual(e[0].role, "entry point")
        self.assertTrue(e[1].trap)
        self.assertEqual(e[2].ref, "feat")

    def test_crlf(self):
        s = maplib.parse_map(("# T\n\n" + MAP).replace("\n", "\r\n"))
        self.assertTrue(s.usable, s.issues)
        self.assertEqual(len(s.entries), 3)

    def test_fenced_marker_ignored(self):
        self.assertIsNone(maplib.parse_map("# T\n\n```\n" + MAP + "```\n"))

    def test_fenced_bullets_ignored(self):
        s = maplib.parse_map("# T\n\n" + MAP + "\n```\n- not a bullet\n```\n")
        self.assertTrue(s.usable, s.issues)

    def test_missing_heading(self):
        s = maplib.parse_map("# T\n\n<!-- wiki-map v1 -->\n- `a.py` — x\n")
        self.assertFalse(s.usable)

    def test_bad_bullets(self):
        for bad in ("- lib/app.py — x", "- `lib/app.py`", "- `lib/app.py` - x"):
            with self.subTest(bad=bad):
                s = maplib.parse_map("<!-- wiki-map v1 -->\n## Where to look\n\n%s\n" % bad)
                self.assertFalse(s.usable)

    def test_empty_map(self):
        s = maplib.parse_map("<!-- wiki-map v1 -->\n## Where to look\n")
        self.assertIn("S18", codes(s))

    def test_too_many(self):
        body = "".join("- `f%d.py` — r\n" % i for i in range(13))
        s = maplib.parse_map("<!-- wiki-map v1 -->\n## Where to look\n\n" + body)
        self.assertIn("S18", codes(s))

    def test_second_section(self):
        s = maplib.parse_map(MAP + "\n" + MAP)
        self.assertIn("S18", codes(s))

    def test_status_page_placement(self):
        before = PAGE.replace("<!-- history -->", MAP + "\n<!-- history -->", 1)
        self.assertFalse(maplib.parse_map(before).usable)
        after = PAGE.rstrip("\n") + "\n\n" + MAP
        self.assertTrue(maplib.parse_map(after).usable, maplib.parse_map(after).issues)

    def test_line_suffix_warns_w8(self):
        s = maplib.parse_map("<!-- wiki-map v1 -->\n## Where to look\n\n- `a.py:12` — x\n")
        self.assertEqual(codes(s), ["W8"])

    def test_symbol_name(self):
        self.assertEqual(maplib.symbol_name("App.start"), "start")
        self.assertEqual(maplib.symbol_name("Type::member"), "member")
        self.assertEqual(maplib.symbol_name("Class#run()"), "run")

    def test_strip_pointers(self):
        out = maplib.strip_pointers("# T\n\n" + MAP)
        self.assertNotIn("`lib/old.py`", out)
        self.assertNotIn("`lib/app.py` `App.start`", out)
        self.assertIn("entry point", out)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class AuditMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = tempfile.TemporaryDirectory()
        cls.addClassCleanup(d.cleanup)
        cls.root = Path(d.name)
        init_git(cls.root)
        (cls.root / "lib").mkdir()
        (cls.root / "lib/app.py").write_text("class App:\n    def start(self): pass\n    def fooBar(self): pass\n")
        (cls.root / "lib/old.py").write_text("x = 1\n")
        write_config(cls.root, main_ref="main")
        make_wiki(cls.root, {"arch.md": "# Arch\n\nbody\n"}, "# Topics\n- [Arch](arch.md) — architecture\n")
        git(cls.root, "add", ".")
        git(cls.root, "commit", "--quiet", "-m", "base")
        git(cls.root, "checkout", "--quiet", "-b", "feat")
        (cls.root / "lib/new.py").write_text("y = 2\n")
        git(cls.root, "add", ".")
        git(cls.root, "commit", "--quiet", "-m", "feat")
        git(cls.root, "checkout", "--quiet", "main")
        cls.git = wl.Git(str(cls.root))

    def section(self, *bullets):
        return maplib.parse_map("<!-- wiki-map v1 -->\n## Where to look\n\n" + "\n".join(bullets) + "\n")

    def audit(self, *bullets):
        return maplib.audit_section(self.section(*bullets), self.git, "main")

    def test_clean(self):
        self.assertEqual(self.audit("- `lib/app.py` `App.start` — x", "- `feat:lib/new.py` — y"), [])

    def test_missing_file_w7(self):
        self.assertEqual([c for c, _ in self.audit("- `lib/gone.py` — x")], ["W7"])

    def test_branch_only_file_not_on_main(self):
        self.assertEqual([c for c, _ in self.audit("- `lib/new.py` — x")], ["W7"])

    def test_directory_is_not_a_file(self):
        self.assertEqual([c for c, _ in self.audit("- `lib` — x")], ["W7"])

    def test_bad_ref_w7(self):
        self.assertEqual([c for c, _ in self.audit("- `nope:lib/app.py` — x")], ["W7"])

    def test_stale_symbol_w8(self):
        self.assertEqual([c for c, _ in self.audit("- `lib/app.py` `App.gone` — x")], ["W8"])

    def test_symbol_whole_word(self):
        self.assertEqual([c for c, _ in self.audit("- `lib/app.py` `App.foo` — x")], ["W8"])
        self.assertEqual(self.audit("- `lib/app.py` `App.fooBar` — x"), [])

    def test_unresolved_main_ref(self):
        out = maplib.audit_section(self.section("- `lib/app.py` — x"), self.git, "nomain")
        self.assertEqual([c for c, _ in out], ["W7"])
        out = maplib.audit_section(self.section("- `feat:lib/new.py` — x"), self.git, "nomain")
        self.assertEqual(out, [])

    def test_audit_cli_reports_map_issues(self):
        page = self.root / WIKI_DIR / "arch.md"
        original = page.read_text()
        try:
            page.write_text(original + "\n" + MAP.replace("`lib/old.py`", "`lib/gone.py`"))
            r = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root)
            self.assertIn("W7 arch.md", r.stdout + r.stderr)
            page.write_text(original + "\n<!-- wiki-map v1 -->\n- bad\n")
            r = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("S18 arch.md", r.stdout + r.stderr)
        finally:
            page.write_text(original)


class LookupMap(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        pages, lines = {}, []
        for i in range(5):
            name = "topic%d.md" % i
            pages[name] = "# Topic %d\n\nzebrafish%d\n\n" % (i, i) + MAP.replace("lib/app.py", "lib/p%d.py" % i)
            lines.append("- [Topic %d](%s) — zebrafish quagga" % (i, name))
        make_wiki(self.root, pages, "\n".join(lines) + "\n")

    def lookup(self, *args):
        return run_tool("lookup.py", *args, "quagga", cwd=self.root, WAYMARK_ROOT=self.root)

    def test_top_three_maps(self):
        out = self.lookup().stdout
        self.assertEqual(out.count("Where to look:"), 3, out)
        self.assertIn("- Trap: `lib/old.py` — legacy", out)
        self.assertIn("- `lib/p0.py` `App.start` — entry point", out)

    def test_blocks_all(self):
        self.assertEqual(self.lookup("--blocks", "all").stdout.count("Where to look:"), 5)

    def test_json_has_map(self):
        import json
        hit = json.loads(self.lookup("--json").stdout)["hits"][0]
        self.assertEqual(hit["map"][0]["pointer"], "lib/p0.py")
        self.assertTrue(hit["map"][1]["trap"])

    def test_invalid_map_not_shown(self):
        page = Path(self.root) / WIKI_DIR / "topic0.md"
        page.write_text("# T\n\n<!-- wiki-map v1 -->\n- bad\n")
        self.assertEqual(self.lookup().stdout.count("Where to look:"), 3)
        out = self.lookup("--blocks", "all").stdout
        self.assertEqual(out.count("Where to look:"), 4)


class HookMap(unittest.TestCase):
    TRAP_FIRST = """<!-- wiki-map v1 -->
## Where to look

- Trap: `lib/old.py` — legacy
- `lib/a.py` `A.run` — first
- `lib/b.py` — second
- `lib/c.py` — fourth, over the cap
"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name

    def wiki(self, pages):
        files = {n + ".md": text for n, text in pages.items()}
        make_wiki(self.root, files, "".join("- [%s](%s) — zebrafish quagga\n" % (n[:-3], n) for n in sorted(files)))
        return pages

    def hook(self, **env):
        r = run_tool("prompt_hook.py", "zebrafish quagga", cwd=self.root, WAYMARK_ROOT=self.root, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def lines(self, out, prefix=("  where:", "  trap:")):
        return [l for l in out.split("\n") if l.startswith(prefix)]

    def test_map_order_kept_traps_included_and_cap_three(self):
        self.wiki({"t": "# T\n\n" + self.TRAP_FIRST})
        got = self.lines(self.hook())
        self.assertEqual(got, ["  trap: `lib/old.py` — legacy",
                               "  where: `lib/a.py` `A.run` — first",
                               "  where: `lib/b.py` — second"])

    def test_shared_budget_drops_later_lines_whole(self):
        pages = {n: "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- `lib/%s.py` — role %s\n" % (n, n)
                 for n in ("a", "b", "c")}
        self.wiki(pages)
        one = len("  where: `lib/a.py` — role a")
        got = self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=2 * one))
        self.assertEqual(len(got), 2, got)
        for l in got:
            self.assertEqual(len(l), one)
        self.assertEqual(len(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=2 * one - 1))), 1)
        self.assertEqual(len(self.lines(self.hook())), 3)

    def test_no_line_is_ever_cut(self):
        self.wiki({"t": "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- `lib/a.py` — %s\n" % ("long " * 20)})
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=40)), [])
        full = self.lines(self.hook())
        self.assertEqual(len(full), 1)
        self.assertTrue(full[0].endswith("long"), full[0])
        self.assertNotIn("…", full[0])

    def test_escaping_counts_toward_the_budget(self):
        self.wiki({"t": "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- `lib/a.py` — <b>x</b>\n"})
        raw = len("  where: `lib/a.py` — <b>x</b>")
        escaped = raw + 4 * 3   # four angle brackets, each grows by 3 characters
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=escaped - 1)), [])
        got = self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=escaped))
        self.assertEqual(len(got), 1)
        self.assertNotIn("<b>", got[0])
        self.assertIn("&lt;b&gt;", got[0])

    def test_zero_disables_and_pages_without_maps_are_unchanged(self):
        self.wiki({"t": "# T\n\n" + self.TRAP_FIRST, "u": "# U\n\nplain\n"})
        off = self.hook(WAYMARK_HOOK_WHERE_CHARS=0)
        self.assertEqual(self.lines(off), [])
        self.wiki({"t": "# T\n\nplain\n", "u": "# U\n\nplain\n"})
        self.assertEqual(self.hook(), self.hook(WAYMARK_HOOK_WHERE_CHARS=0))
        self.assertNotIn("where:", self.hook())

    def test_invalid_map_gives_no_where_line(self):
        self.wiki({"t": "# T\n\n<!-- wiki-map v1 -->\n- bad\n"})
        self.assertNotIn("where:", self.hook())


@unittest.skipUnless(shutil.which("git"), "Git is required")
class MapTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = tempfile.TemporaryDirectory()
        cls.addClassCleanup(d.cleanup)
        r = cls.root = Path(d.name)
        init_git(r)
        write_config(r, main_ref="main")
        (r / "lib").mkdir()

        def commit(msg, **files):
            for name, text in files.items():
                path = r / name.replace("__", "/")
                path.parent.mkdir(parents=True, exist_ok=True)
                if text is None:
                    path.unlink()
                else:
                    path.write_bytes(text if isinstance(text, bytes) else text.encode())
            git(r, "add", "-A")
            git(r, "commit", "--quiet", "-m", msg)

        # root commit, matches WA-1
        commit("WA-1 add hot and cold", lib__hot="1\n", lib__cold="c\n", lib__temp="t\n",
               **{".claude__memory__note.md": "# n\n"})
        commit("wa-1 tweak hot", lib__hot="1\n2\n3\n")
        commit("unrelated change", lib__other="o\n")
        commit("WA-1 move cold", lib__cold=None, lib__moved="c\n")
        commit("WA-1 drop temp, add blob", lib__temp=None, lib__bin=b"\x00\x01\x02")
        git(r, "checkout", "--quiet", "-b", "side")
        commit("side work", lib__side="s\n")
        git(r, "checkout", "--quiet", "main")
        commit("main work", lib__mainonly="m\n")
        git(r, "merge", "--quiet", "--no-ff", "-m", "Merge WA-1 side branch", "side")
        git(r, "checkout", "--quiet", "-b", "feat")
        commit("feat", lib__feat="f\n", **{".claude__memory__feat.md": "# f\n"})
        git(r, "checkout", "--quiet", "main")

    def run_map(self, *args, cwd=None):
        return run_tool("map.py", *args, cwd=cwd or self.root)

    def paths(self, r):
        return [l.split("`")[1] for l in r.stdout.split("\n") if l.startswith("- `")]

    def test_template_needs_no_wiki(self):
        with tempfile.TemporaryDirectory() as empty:
            r = self.run_map("template", cwd=empty)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(maplib.parse_map(r.stdout.replace("path/to/", "x/")).usable)

    def test_term_match_case_insensitive_and_ranked(self):
        r = self.run_map("suggest", "wa-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = self.paths(r)
        self.assertEqual(got[0], "lib/hot")           # two matching commits beat one
        self.assertTrue(maplib.parse_map(r.stdout.split("\n\nCommits used")[0]).usable)
        self.assertIn("— TODO", r.stdout)
        self.assertIn("Commits used (", r.stdout)

    def test_rank_ties_break_on_lines(self):
        got = self.paths(self.run_map("suggest", "WA-1"))
        # one matching commit each: side has 1 changed line, a pure rename and a binary file have 0
        self.assertLess(got.index("lib/side"), got.index("lib/bin"))
        self.assertLess(got.index("lib/bin"), got.index("lib/moved"))

    def test_term_is_a_fixed_string(self):
        self.assertEqual(self.run_map("suggest", "WA.1").returncode, 1)
        self.assertEqual(self.run_map("suggest", "no-such-term").returncode, 1)

    def test_rename_counts_new_path_deleted_and_wiki_dropped(self):
        got = self.paths(self.run_map("suggest", "WA-1"))
        self.assertIn("lib/moved", got)
        self.assertNotIn("lib/cold", got)
        self.assertNotIn("lib/temp", got)
        self.assertFalse([g for g in got if g.startswith(".claude/")], got)

    def test_binary_file_is_listed(self):
        self.assertIn("lib/bin", self.paths(self.run_map("suggest", "WA-1")))

    def test_merge_commit_diffs_against_first_parent(self):
        r = self.run_map("suggest", "Merge WA-1 side")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.paths(r), ["lib/side"])

    def test_root_commit_diffs_against_empty_tree(self):
        r = self.run_map("suggest", "add hot and cold")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = self.paths(r)
        self.assertIn("lib/hot", got)
        self.assertNotIn("lib/cold", got)   # moved away
        self.assertNotIn("lib/temp", got)   # deleted

    def test_commits_option(self):
        sha = git(self.root, "log", "--format=%H", "--grep=unrelated")
        r = self.run_map("suggest", "--commits", sha)
        self.assertEqual(self.paths(r), ["lib/other"])
        self.assertEqual(self.run_map("suggest", "--commits", "nope123").returncode, 2)

    def test_max_and_ref(self):
        self.assertEqual(len(self.paths(self.run_map("suggest", "--max", "2", "WA-1"))), 2)
        r = self.run_map("suggest", "--ref", "feat", "feat")
        self.assertEqual(self.paths(r), ["lib/feat"])      # wiki file dropped
        self.assertEqual(self.run_map("suggest", "--ref", "feat", "unrelated").returncode, 0)
        self.assertEqual(self.run_map("suggest", "--ref", "nope", "x").returncode, 2)

    def test_branch_uses_ref_pointers(self):
        r = self.run_map("suggest", "--branch", "feat")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.paths(r), ["feat:lib/feat"])

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_map("suggest").returncode, 2)
        self.assertEqual(self.run_map("suggest", "--branch", "feat", "WA-1").returncode, 2)
        self.assertEqual(self.run_map("suggest", "--branch", "nope").returncode, 2)
        self.assertEqual(self.run_map("suggest", "--max", "0", "WA-1").returncode, 2)

    def test_parse_numstat_rename_and_binary(self):
        raw = b"3\t1\tlib/a\0-\t-\tlib/bin\0" + b"2\t0\t\0lib/old\0lib/new\0" + b"1\t1\tlib/z\0"
        self.assertEqual(maplib_free_parse(raw), [("lib/a", 4), ("lib/bin", 0), ("lib/new", 2), ("lib/z", 2)])


if __name__ == "__main__":
    unittest.main()
