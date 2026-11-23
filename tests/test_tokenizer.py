import shutil
import unittest
from pathlib import Path
from unittest.mock import call, patch

from helpers import WIKI_DIR, fixture, sync_fixture, wl


class Tokenizer(unittest.TestCase):
    def test_identifiers(self):
        got = {wl.fold(t) for t in wl.identifiers(
            "ImportSizeLimit BatchConfig.DEFAULT_LIMIT rc-2.4.1 v1.2.3 1a2b3c4d5 batch_queue")}
        for want in ("importsizelimit", "batchconfig.default_limit", "rc-2.4.1", "v1.2.3",
                     "1a2b3c4d5", "batch_queue"):
            self.assertIn(want, got)
        self.assertEqual(wl.identifiers("abcdefabcdef are we ready to ship"), [])

    def test_version_boundary(self):
        rx = wl.ident_regex("rc-2.4.1")
        self.assertTrue(rx.search("tag rc-2.4.1 shipped"))
        self.assertTrue(rx.search("tag rc-2.4.1."))
        self.assertFalse(rx.search("tag rc-2.4.10"))
        self.assertFalse(rx.search("tag rc-2.4.1.2"))

    def test_versions_exclude_plain_decimals(self):
        self.assertEqual(wl.identifiers("0.5 `1.5` 10.30"), [])
        self.assertEqual(wl.identifiers("v1.2 rel-1.2.3 1.2.3"), ["v1.2", "rel-1.2.3", "1.2.3"])


class Qualify(unittest.TestCase):
    def setUp(self):
        self.d = fixture()
        self.entries = wl.load_entries(self.d)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_single_ident_qualifies_in_hook(self):
        hits, _, _ = wl.search(self.entries, "DemoThing", mode="hook")
        self.assertTrue(hits)

    def test_single_plain_word_hook_vs_cli(self):
        self.assertEqual(wl.search(self.entries, "continue ready", mode="hook")[0], [])
        self.assertTrue(wl.search(self.entries, "ready", mode="cli")[0])

    def test_version_ident_exact(self):
        hits, _, _ = wl.search(self.entries, "rc-2.4.1", mode="hook")
        self.assertEqual([h.entry.page for h in hits], [])  # only rc-2.4.10 is indexed so far

    def test_body_search_preserves_folding_with_an_ascii_fast_path(self):
        wiki = Path(self.d) / WIKI_DIR
        ascii_page = "# Example\nimportsizelimit\n"
        (wiki / "ascii.md").write_text(ascii_page, encoding="utf-8")
        (wiki / "accented.md").write_text("# Example\nImpórtSizeLimit\n", encoding="utf-8")
        with patch.object(wl, "fold", wraps=wl.fold) as folding:
            self.assertEqual(wl.body_search(self.d, ["ImportSizeLimit"]), {
                "ImportSizeLimit": [("accented.md", 1, [2]), ("ascii.md", 1, [2])],
            })
        self.assertNotIn(call(ascii_page), folding.call_args_list)


class Hygiene(unittest.TestCase):
    def test_filenames_and_api_names_are_not_identifiers(self):
        for q in ("update index.md", "read MEMORY.md", "edit the CLAUDE.md file", "fix scripts/audit.py",
                  "call time.sleep here", "use json.dumps now"):
            self.assertEqual(wl.identifiers(q), [], q)
        self.assertEqual([wl.fold(t) for t in wl.identifiers("where is ImportSizeLimit set")],
                         ["importsizelimit"])

    def test_ident_cap(self):
        many = " ".join("Alpha%dBeta%dGamma" % (i, i) for i in range(500))
        self.assertLessEqual(len(wl.identifiers(many)), wl.MAX_IDENTS)

    def test_status_head_does_not_match(self):
        d = fixture()
        try:
            sync_fixture(d)
            entries = wl.load_entries(d)
            for q in ("merge the branch", "is the code merged", "what was the decision on the code"):
                hits, _, _ = wl.search(entries, q, mode="hook")
                self.assertFalse([h for h in hits if h.entry.page == "demo.md"], q)
        finally:
            shutil.rmtree(d)
