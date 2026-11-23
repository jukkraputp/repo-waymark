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
