"""Parsing and audit checks for the optional "Where to look" map."""
from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import PAGE, WIKI_DIR, git, init_git, make_wiki, run_tool, write_config
import maplib
import wikilib as wl

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
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        init_git(self.root)
        write_config(self.root, main_ref="main")
        (self.root / "lib").mkdir()
        for i in range(3):
            (self.root / "lib/hot.py").write_text("v%d\n" % i)
            (self.root / "lib/cold.py").write_text("c\n")
            git(self.root, "add", ".")
            git(self.root, "commit", "--quiet", "-m", "c%d" % i)
        git(self.root, "checkout", "--quiet", "-b", "feat")
        (self.root / "lib/new.py").write_text("n\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "--quiet", "-m", "feat")
        git(self.root, "checkout", "--quiet", "main")

    def run_map(self, *args, cwd=None):
        return run_tool("map.py", *args, cwd=cwd or self.root)

    def test_template_needs_no_wiki(self):
        with tempfile.TemporaryDirectory() as empty:
            r = self.run_map("template", cwd=empty)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(maplib.parse_map(r.stdout.replace("path/to/", "x/")).usable)

    def test_suggest_ranks_by_change_count(self):
        r = self.run_map("suggest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.stdout.index("lib/hot.py"), r.stdout.index("lib/cold.py") if "lib/cold.py" in r.stdout else 10**9)
        self.assertTrue(maplib.parse_map(r.stdout).usable)

    def test_suggest_branch_uses_ref_pointers(self):
        r = self.run_map("suggest", "--ref", "feat")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`feat:lib/new.py`", r.stdout)
        self.assertNotIn("hot.py", r.stdout)

    def test_suggest_unrelated_history_uses_empty_tree(self):
        git(self.root, "checkout", "--quiet", "--orphan", "other")
        git(self.root, "rm", "-rfq", "--cached", ".")
        (self.root / "solo.py").write_text("s\n")
        git(self.root, "add", "solo.py")
        git(self.root, "commit", "--quiet", "-m", "solo")
        git(self.root, "checkout", "--quiet", "-f", "main")
        r = self.run_map("suggest", "--ref", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`other:solo.py`", r.stdout)

    def test_suggest_bad_ref_is_usage_error(self):
        self.assertEqual(self.run_map("suggest", "--ref", "nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
