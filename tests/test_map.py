"""Parsing and audit checks for the optional "Where to look" map."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import PAGE, WIKI_DIR, git, init_git, make_wiki, run_tool, subprocess_env, write_config
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

    def test_pointer_text_is_not_scanned_for_wikilinks_or_release_tags(self):
        page = self.root / WIKI_DIR / "arch.md"
        original = page.read_text()
        try:
            write_config(self.root, main_ref="main", release_tag_pattern=r"v\d+\.\d+\.\d+")
            bullets = ("- `src/[[helper-fn]].py` `v9.9.9` — role\n- `vendor/v8.8.8/a.py`\n"
                       "- `[x](v7.7.7/y.py)` — link-shaped\n")
            dup = "<!-- wiki-map v1 -->\n## Where to look\n\n" + bullets
            page.write_text(original + "\n" + dup + "\n" + dup)
            r = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root)
            out = r.stdout + r.stderr
            self.assertIn("BROKEN WIKILINKS 0", out)
            self.assertNotIn("G1", out)
            self.assertNotIn("G2", out)
            self.assertIn("S18 arch.md", out)   # the findings that belong to the map are still there
            page.write_text(original + "\n<!-- x -->\n\n<!-- wiki-map v1 -->\n## Where to look\n\n"
                            "- `src/a.py` `v9.9.9` — replaced by `v6.6.6`\n")
            out = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root).stdout
            self.assertIn("v6.6.6", out)       # role text is still scanned for release tags
            self.assertNotIn("v9.9.9", out)    # the symbol is not
            # without masking this prose would be flagged
            page.write_text(original + "\nSee [[helper-fn]] and v9.9.9.\n")
            out = run_tool("audit.py", cwd=self.root, WAYMARK_ROOT=self.root).stdout
            self.assertIn("[[helper-fn]]", out)
            self.assertIn("G2", out)
        finally:
            page.write_text(original)
            write_config(self.root, main_ref="main")

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

    LONG = ("Reads the manifest and decides which entries to refresh, then writes the cache file back "
            "to disk before the process exits")

    def one_page(self, role, pointer="lib/a.py", symbol=""):
        self.wiki({"t": "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- `%s`%s — %s\n"
                   % (pointer, " `%s`" % symbol if symbol else "", role)})

    def test_shared_budget_drops_later_lines_whole(self):
        pages = {n: "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- `lib/%s.py` — role %s\n" % (n, n)
                 for n in ("a", "b", "c")}
        self.wiki(pages)
        one = len("  where: `lib/a.py`") + 1   # pointer and symbol only; each line is charged its newline
        env = {"WAYMARK_HOOK_WHERE_ROLE_CHARS": 0}
        got = self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=2 * one, **env))
        self.assertEqual(len(got), 2, got)
        for l in got:
            self.assertEqual(len(l) + 1, one)
        self.assertEqual(len(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=2 * one - 1, **env))), 1)
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=one - 1, **env)), [])
        self.assertEqual(len(self.lines(self.hook())), 3)

    def test_long_role_is_trimmed_at_a_word_boundary_with_an_ellipsis(self):
        self.one_page(self.LONG, symbol="Cache.refresh")
        [line] = self.lines(self.hook())
        role = line.split(" — ", 1)[1]
        self.assertLessEqual(len(role), 90)
        self.assertTrue(role.endswith("…"), role)
        self.assertTrue(self.LONG.startswith(role[:-1]), role)
        self.assertEqual(self.LONG[len(role) - 1], " ")   # cut on a word boundary
        self.assertTrue(line.startswith("  where: `lib/a.py` `Cache.refresh` — "))
        short = self.lines(self.hook(WAYMARK_HOOK_WHERE_ROLE_CHARS=30))[0].split(" — ", 1)[1]
        self.assertLessEqual(len(short), 30)
        self.assertTrue(short.endswith("…"))
        self.one_page("short role")
        self.assertEqual(self.lines(self.hook()), ["  where: `lib/a.py` — short role"])

    def test_role_is_dropped_when_the_trimmed_line_does_not_fit(self):
        self.one_page(self.LONG, symbol="Cache.refresh")
        bare = "  where: `lib/a.py` `Cache.refresh`"
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=len(bare) + 1)), [bare])
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=len(bare))), [])   # pointer never cut
        full = self.lines(self.hook())[0]
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=len(full))), [bare])   # newline counts
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=len(full) + 1)), [full])

    def test_trap_entry_falls_back_to_pointer_only(self):
        self.wiki({"t": "# T\n\n<!-- wiki-map v1 -->\n## Where to look\n\n- Trap: `lib/old.py` — %s\n" % self.LONG})
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=len("  trap: `lib/old.py`") + 1)),
                         ["  trap: `lib/old.py`"])

    def test_role_chars_zero_shows_pointer_and_symbol_only(self):
        self.one_page("a role", symbol="A.b")
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_ROLE_CHARS=0)), ["  where: `lib/a.py` `A.b`"])

    def test_a_long_pointer_is_dropped_never_cut(self):
        self.one_page("r", pointer="lib/" + "x" * 80 + ".py")
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=60)), [])
        self.assertEqual(len(self.lines(self.hook())), 1)

    def test_escaping_counts_toward_the_budget(self):
        self.one_page("<b>x</b>")
        bare = len("  where: `lib/a.py`")
        raw = len("  where: `lib/a.py` — <b>x</b>")
        escaped = raw + 4 * 3 + 1   # four angle brackets, each grows by 3 characters, plus the newline
        short = self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=escaped - 1))
        self.assertEqual(short, ["  where: `lib/a.py`"])   # the escaped role no longer fits
        got = self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=escaped))
        self.assertEqual(len(got), 1)
        self.assertNotIn("<b>", got[0])
        self.assertIn("&lt;b&gt;", got[0])
        self.assertEqual(self.lines(self.hook(WAYMARK_HOOK_WHERE_CHARS=bare)), [])

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
        commit("UNI-1 non-ascii name", **{"lib__caf\u00e9 \"q\"": "u\n"})
        try:
            os.symlink("hot", r / "lib" / "link")
            commit("TYPE-1 link", lib__x="x\n")
            (r / "lib" / "link").unlink()
            commit("TYPE-1 link becomes a file", lib__link="now a file\n")
        except OSError:
            pass
        git(r, "checkout", "--quiet", "-b", "feat")
        commit("feat", lib__feat="f\n", **{".claude__memory__feat.md": "# f\n"})
        git(r, "checkout", "--quiet", "main")
        git(r, "branch", "same")   # a second name for the tip commit of main

    def run_map(self, *args, cwd=None):
        return run_tool("map.py", *args, cwd=cwd or self.root)

    def paths(self, r):
        return [l.split("`")[1] for l in r.stdout.split("\n") if l.startswith("- `")]

    def test_template_needs_no_wiki(self):
        with tempfile.TemporaryDirectory() as empty:
            r = self.run_map("template", cwd=empty)
        self.assertEqual(r.returncode, 0, r.stderr)
        page, _, index_line = r.stdout.partition("\nIndex line to add")
        self.assertTrue(page.startswith("# Page title\n\nOne-line About"))
        self.assertTrue(maplib.parse_map(page.replace("path/to/", "x/")).usable)
        self.assertIn("- [Page title](page.md) — About of this topic. · Keywords:", index_line)

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

    def test_commit_option_is_repeatable(self):
        one = git(self.root, "log", "--format=%H", "--grep=unrelated")
        two = git(self.root, "log", "--format=%H", "--grep=tweak hot")
        self.assertEqual(self.paths(self.run_map("suggest", "--commit", one)), ["lib/other"])
        both = self.paths(self.run_map("suggest", "--commit", one, "--commit", two))
        self.assertEqual(sorted(both), ["lib/hot", "lib/other"])
        self.assertEqual(self.run_map("suggest", "--commit", "nope123").returncode, 2)
        self.assertEqual(self.run_map("suggest", "--commit", one, "--commit", "nope123").returncode, 2)
        self.assertEqual(self.run_map("suggest", "--commits", one).returncode, 2)

    def test_terms_are_ored_in_one_search(self):
        got = self.paths(self.run_map("suggest", "unrelated", "tweak hot"))
        self.assertEqual(sorted(got), ["lib/hot", "lib/other"])

    def test_double_dash_keeps_a_term_literal(self):
        r = self.run_map("suggest", "--", "--root")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("usage", r.stderr)

    def test_max_and_ref(self):
        self.assertEqual(len(self.paths(self.run_map("suggest", "--max", "2", "WA-1"))), 2)
        r = self.run_map("suggest", "--ref", "feat", "feat")
        self.assertEqual(self.paths(r), ["feat:lib/feat"])   # another commit than main_ref: ref pointer
        self.assertEqual(self.paths(self.run_map("suggest", "--ref", "main", "unrelated")), ["lib/other"])
        self.assertEqual(self.paths(self.run_map("suggest", "--ref", "feat", "unrelated")), ["feat:lib/other"])
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

    def test_ref_is_qualified_by_name_even_when_it_points_at_main(self):
        r = self.run_map("suggest", "--ref", "same", "unrelated")
        self.assertEqual(self.paths(r), ["same:lib/other"])
        self.assertEqual(self.paths(self.run_map("suggest", "--ref", "main", "unrelated")), ["lib/other"])

    def test_tree_read_failure_is_exit_2(self):
        original = wl.Git.run

        def run(self, *args, **kwargs):
            return (128, "") if args and args[0] == "ls-tree" else original(self, *args, **kwargs)

        with mock.patch.object(wl.Git, "run", run), mock.patch("sys.stderr"):
            self.assertEqual(maptool.main(["--root", str(self.root), "suggest", "WA-1"]), 2)
            self.assertEqual(maptool.main(["--root", str(self.root), "suggest", "--branch", "feat"]), 2)
        self.assertEqual(self.run_map("suggest", "WA-1").returncode, 0)

    def test_template_takes_no_suggest_options_and_empty_values_are_usage_errors(self):
        self.assertEqual(self.run_map("template", "--commit", "HEAD").returncode, 2)
        self.assertEqual(self.run_map("template", "--max", "3").returncode, 2)
        self.assertEqual(self.run_map("template").returncode, 0)
        for flag in ("--branch=", "--ref=", "--commit="):
            with self.subTest(flag=flag):
                r = self.run_map("suggest", flag, "WA-1") if flag != "--branch=" else self.run_map("suggest", flag)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_non_ascii_and_quoted_names_survive(self):
        r = self.run_map("suggest", "UNI-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.paths(r), ['lib/caf\u00e9 "q"'])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_type_change_is_counted(self):
        r = self.run_map("suggest", "TYPE-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lib/link", self.paths(r))   # regular file at the ref after the type change

    def test_git_failure_is_exit_2_not_nothing_found(self):
        with self.assertRaises(maptool.GitFailure):
            maptool.run_bytes(wl.Git(str(self.root)), "no-such-subcommand")
        with mock.patch.object(maptool, "search_commits", side_effect=maptool.GitFailure("boom")), \
                mock.patch("sys.stderr"):
            self.assertEqual(maptool.main(["--root", str(self.root), "suggest", "WA-1"]), 2)

    def test_git_calls_pin_rename_detection(self):
        self.assertIn("diff.renames=true", maptool.GIT_CONFIG)

    def test_shallow_boundary_is_skipped_not_diffed_against_the_empty_tree(self):
        with tempfile.TemporaryDirectory() as d:
            clone = Path(d) / "clone"
            subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + str(self.root), str(clone)],
                           check=True, capture_output=True, env=subprocess_env())
            write_config(clone, main_ref="main")
            r = self.run_map("suggest", "TYPE-1", cwd=clone)   # the boundary commit is the tip
            self.assertIn("shallow", r.stderr, r.stderr)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertEqual(self.paths(r), [])

    def test_parse_numstat_rename_and_binary(self):
        raw = b"3\t1\tlib/a\0-\t-\tlib/bin\0" + b"2\t0\t\0lib/old\0lib/new\0" + b"1\t1\tlib/z\0"
        self.assertEqual(maplib_free_parse(raw), [("lib/a", 4), ("lib/bin", 0), ("lib/new", 2), ("lib/z", 2)])


class ParserEdges(unittest.TestCase):
    HEAD = "<!-- wiki-map v1 -->\n## Where to look\n\n"

    def test_backtick_fence_with_backtick_in_info_is_not_a_fence(self):
        text = "``` not `a fence`\n" + self.HEAD + "- `a.py` — x\n"
        self.assertTrue(maplib.parse_map(text).usable)
        fenced = "```py\n" + self.HEAD + "- `a.py` — x\n```\n"
        self.assertIsNone(maplib.parse_map(fenced))
        self.assertEqual(maplib._fenced_lines(["``` a`b", "x"]), [False, False])

    def test_indented_h2_ends_the_section(self):
        s = maplib.parse_map(self.HEAD + "- `a.py` — x\n   ## Next\n- `b.py` — y\n")
        self.assertTrue(s.usable, s.issues)
        self.assertEqual([e.path for e in s.entries], ["a.py"])

    def test_other_list_markers_are_s18(self):
        for marker in ("* ", "+ ", "-\t"):
            with self.subTest(marker=repr(marker)):
                s = maplib.parse_map(self.HEAD + "- `a.py` — x\n" + marker + "`b.py` — y\n")
                self.assertFalse(s.usable)
                self.assertIn("S18", codes(s))

    def test_mask_pointers_covers_invalid_and_duplicate_maps(self):
        text = (self.HEAD + "- `src/[[helper-fn]].py` `v9.9.9` — no role\n" + "- `x/[[other-one]] unclosed\n\n"
                + self.HEAD + "- `vendor/v1.2.3/a.py` — dup\n")
        out = maplib.mask_pointers(text)
        for gone in ("[[helper-fn]]", "v9.9.9", "[[other-one]]", "v1.2.3"):
            self.assertNotIn(gone, out)
        self.assertEqual(out.count("\n"), text.count("\n"))

    def test_mask_keeps_role_text_and_stops_at_the_parser_boundaries(self):
        text = (self.HEAD + "- `a/[[hidden-one]].py` `v1.0.0` — role with `v9.9.9` and [[role-link]]\n"
                "<!-- note -->\n- `b/[[after-comment]].py` — x\n"
                "## Other\n- `c/[[after-h2]].py` — y\n")
        out = maplib.mask_pointers(text)
        self.assertNotIn("[[hidden-one]]", out)
        self.assertNotIn("v1.0.0", out)
        self.assertIn("`v9.9.9`", out)
        self.assertIn("[[role-link]]", out)
        self.assertIn("[[after-comment]]", out)
        self.assertIn("[[after-h2]]", out)
        self.assertEqual(maplib.mask_pointers("# T\n\nnothing\n"), "# T\n\nnothing\n")

    def test_indented_heading_is_accepted(self):
        for heading in ("  ## Where to look", "   ## Where to look  "):
            s = maplib.parse_map("<!-- wiki-map v1 -->\n" + heading + "\n\n- `a.py` — x\n")
            self.assertTrue(s.usable, (heading, s.issues))
        self.assertFalse(maplib.parse_map("<!-- wiki-map v1 -->\n    ## Where to look\n\n- `a.py` — x\n").usable)

    def test_indented_and_ordered_items_are_s18_after_a_valid_bullet(self):
        for item in ("  * `b.py` — y", " + `b.py` — y", "1. `b.py` — y", "2) `b.py` — y", "  - `b.py` — y"):
            with self.subTest(item=item):
                s = maplib.parse_map(self.HEAD + "- `a.py` — x\n" + item + "\n")
                self.assertFalse(s.usable)
                self.assertIn("S18", codes(s))
        ok = maplib.parse_map(self.HEAD + "- `a.py` — x\n    continuation text\n\nplain prose\n")
        self.assertTrue(ok.usable, ok.issues)

    def test_map_for_page_only_reads_top_level_files_inside_the_wiki(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wiki = root / WIKI_DIR
            wiki.mkdir(parents=True)
            body = "# T\n\n" + self.HEAD + "- `a.py` — x\n"
            (wiki / "ok.md").write_text(body)
            (wiki / "sub").mkdir()
            (wiki / "sub" / "deep.md").write_text(body)
            (root / ".claude" / "guide.md").write_text(body)
            outside = root / "outside.md"
            outside.write_text(body)
            try:
                os.symlink(str(outside), str(wiki / "link.md"))
            except OSError:
                pass
            cfg = wl.config_for(str(root))
            self.assertIsNotNone(maplib.map_for_page(str(root), cfg, "ok.md"))
            for name in ("MEMORY.md", "index.md", "index-a.md"):
                (wiki / name).write_text(body)
                self.assertIsNone(maplib.map_for_page(str(root), cfg, name), name)
            for bad in ("../guide.md", "sub/deep.md", "link.md", "/etc/passwd", "ok.txt", "missing.md", "..", ""):
                with self.subTest(bad=bad):
                    self.assertIsNone(maplib.map_for_page(str(root), cfg, bad))


class ContainmentAndOrder(unittest.TestCase):
    MAPTEXT = "<!-- wiki-map v1 -->\n## Where to look\n\n- `lib/a.py` `A.run` — entry\n"

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name

    def test_lookup_and_hook_ignore_an_index_link_outside_the_wiki(self):
        make_wiki(self.root, {"in.md": "# In\n\nplain\n"},
                  "- [Guide](../guide.md) — zebrafish quagga\n- [In](in.md) — zebrafish quagga\n")
        (Path(self.root) / ".claude" / "guide.md").write_text("# G\n\n" + self.MAPTEXT)
        out = run_tool("lookup.py", "quagga", cwd=self.root, WAYMARK_ROOT=self.root).stdout
        self.assertNotIn("Where to look", out)
        hook = run_tool("prompt_hook.py", "zebrafish quagga", cwd=self.root, WAYMARK_ROOT=self.root).stdout
        self.assertNotIn("where:", hook)

    def test_lookup_prints_the_map_before_the_status_block_and_json_has_path_and_ref(self):
        page = PAGE.replace("<!-- history -->", "<!-- history -->", 1) + "\n" + self.MAPTEXT + "- `feat:lib/n.py` — new\n"
        make_wiki(self.root, {"demo.md": page}, "- [Demo](demo.md) — zebrafish quagga\n")
        sync = run_tool("status.py", "sync", cwd=self.root, WAYMARK_ROOT=self.root)
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        out = run_tool("lookup.py", "quagga demo", cwd=self.root, WAYMARK_ROOT=self.root).stdout
        self.assertIn("Where to look:", out, out)
        self.assertLess(out.index("Where to look:"), out.index("Current status"))
        data = json.loads(run_tool("lookup.py", "--json", "quagga demo", cwd=self.root, WAYMARK_ROOT=self.root).stdout)
        entries = data["hits"][0]["map"]
        self.assertEqual((entries[0]["pointer"], entries[0]["path"], entries[0]["ref"]), ("lib/a.py", "lib/a.py", None))
        self.assertEqual((entries[1]["path"], entries[1]["ref"]), ("lib/n.py", "feat"))

    def test_json_has_no_three_hit_cap(self):
        pages = {"t%d.md" % i: "# T%d\n\n" % i + self.MAPTEXT for i in range(5)}
        make_wiki(self.root, pages, "".join("- [T%d](t%d.md) — zebrafish quagga\n" % (i, i) for i in range(5)))
        data = json.loads(run_tool("lookup.py", "--json", "quagga", cwd=self.root, WAYMARK_ROOT=self.root).stdout)
        self.assertEqual(sum(1 for h in data["hits"] if "map" in h), 5)
        text = run_tool("lookup.py", "quagga", cwd=self.root, WAYMARK_ROOT=self.root).stdout
        self.assertEqual(text.count("Where to look:"), 3)


if __name__ == "__main__":
    unittest.main()
