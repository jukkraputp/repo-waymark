from pathlib import Path
import shutil
import unittest

from helpers import DATE, WIKI_DIR, fixture, status, wl


class StatusSync(unittest.TestCase):
    def test_sync_is_idempotent_and_local(self):
        d = fixture()
        self.addCleanup(shutil.rmtree, d)
        idx = Path(d) / WIKI_DIR / "index-a.md"
        before_other = [line for line in wl.read(idx).splitlines() if "other.md" in line]
        self.assertEqual(status.sync(d, []), 0)
        once = wl.read(idx)
        self.assertEqual(status.sync(d, []), 0)
        self.assertEqual(wl.read(idx), once)
        self.assertEqual([line for line in once.splitlines() if "other.md" in line], before_other)
        self.assertEqual(status.check(d), [])
        self.assertIn(f"**Status {DATE}:** decision approved · code committed · merged no · tested none"
                      " · published no — Built, not merged.", once)


class CrlfSync(unittest.TestCase):
    def test_crlf_preserved(self):
        d = fixture()
        self.addCleanup(shutil.rmtree, d)
        m = Path(d) / WIKI_DIR
        for name in ("index-a.md", "demo.md"):
            wl.write_text(m / name, wl.read(m / name), True)
        self.assertEqual(status.sync(d, []), 0)
        raw = (m / "index-a.md").read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertEqual(status.check(d), [])

    def test_sync_preserves_invalid_utf8_outside_generated_lines(self):
        d = fixture()
        self.addCleanup(shutil.rmtree, d)
        wiki = Path(d) / WIKI_DIR
        paths = (wiki / "index-a.md", wiki / "demo.md")
        paths[1].write_bytes(b"---\r\ndescription: Outdated\n---\r\n\n" + paths[1].read_bytes())
        unrelated = b"\nHistorical Caf\xe9 note.\n"
        for path in paths:
            path.write_bytes(path.read_bytes() + unrelated)
        self.assertEqual(status.sync(d, []), 0)
        for path in paths:
            self.assertTrue(path.read_bytes().endswith(unrelated), path.name)
        self.assertTrue(paths[1].read_bytes().startswith(b"---\r\ndescription: A demo.\n---\r\n"))
        self.assertEqual(status.check(d), [])


class DescriptionYaml(unittest.TestCase):
    def setUp(self):
        self.root = fixture()
        self.addCleanup(shutil.rmtree, self.root)
        self.page = Path(self.root) / WIKI_DIR / "demo.md"
        self.assertEqual(status.sync(self.root, []), 0)

    def write(self, front_matter, about="A demo."):
        text = wl.read(self.page).replace("- **About:** A demo.", "- **About:** " + about)
        self.page.write_text("---\n" + front_matter + "---\n\n" + text, encoding="utf-8")

    def front_matter(self):
        return wl.read(self.page).split("---\n")[1]

    def test_sync_quotes_description_that_is_not_plain_yaml(self):
        about = 'Cache: invalidation and "quoted" \\ paths'
        self.write("title: Demo\ndescription: Old\n", about)
        self.assertEqual(status.sync(self.root, []), 0)
        self.assertEqual(self.front_matter(),
                         'title: Demo\ndescription: "Cache: invalidation and \\"quoted\\" \\\\ paths"\n')
        self.assertEqual(status.read_yaml_scalar(self.front_matter().split("description:")[1]), about)
        self.assertEqual(status.check(self.root), [])

    def test_check_reports_unquoted_invalid_description(self):
        self.write("description: Cache: invalidation\n", "Cache: invalidation")
        self.assertTrue(any("front-matter description" in p for p in status.check(self.root)))
        self.assertEqual(status.sync(self.root, []), 0)
        self.assertEqual(self.front_matter(), 'description: "Cache: invalidation"\n')
        self.assertEqual(status.check(self.root), [])

    def test_valid_plain_and_quoted_descriptions_stay_in_sync(self):
        for line in ("description: A demo.", 'description: "A demo."', "description: 'A demo.'"):
            with self.subTest(line=line):
                self.write(line + "\n")
                before = wl.read_raw(self.page)
                self.assertEqual(status.check(self.root), [])
                self.assertEqual(status.sync(self.root, []), 0)
                self.assertEqual(wl.read_raw(self.page), before)

    def test_multiline_description_is_replaced_as_a_unit(self):
        self.write("description: >-\n  An old\n\n  folded text\ntitle: Demo\n")
        self.assertTrue(any("front-matter description" in p for p in status.check(self.root)))
        self.assertEqual(status.sync(self.root, []), 0)
        self.assertEqual(self.front_matter(), "description: A demo.\ntitle: Demo\n")
        self.assertEqual(status.check(self.root), [])
