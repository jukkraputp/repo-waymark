"""Shared fixtures for Waymark's stdlib test suite."""

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import status
import wikilib as wl

WIKI_DIR = Path(".claude/memory")
DATE = datetime.date.today().isoformat()
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wiki"
PAGE = (FIXTURES / "demo.md").read_text(encoding="utf-8").replace("@DATE@", DATE)


def fixture():
    root = tempfile.mkdtemp()
    make_wiki(
        root,
        {"demo.md": PAGE, "other.md": (FIXTURES / "other.md").read_text(encoding="utf-8")},
        (FIXTURES / "index-a.md").read_text(encoding="utf-8"),
    )
    return root


def make_wiki(tmp, pages, index, config=None):
    """Write the supplied topic pages and category indexes below a temporary root."""
    root = Path(tmp)
    wiki = root / (config or {}).get("wiki_dir", WIKI_DIR)
    wiki.mkdir(parents=True, exist_ok=True)
    indexes = {"index-a.md": index} if isinstance(index, str) else index
    for name, text in {**pages, **indexes}.items():
        (wiki / name).write_text(text, encoding="utf-8")
    if config is not None:
        write_config(root, **config)
    return wiki


def subprocess_env(**overrides):
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        PYTHONIOENCODING="utf-8",
    )
    env.update({key: str(value) for key, value in overrides.items() if value is not None})
    return env


def run_tool(script, *args, cwd, input_text=None, **overrides):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
        cwd=cwd,
        env=subprocess_env(**overrides),
    )


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=subprocess_env(),
    ).stdout.strip()


def init_git(root):
    template = Path(root) / "empty-git-template"
    template.mkdir()
    git(root, "init", "--quiet", "-b", "main", f"--template={template}")
    template.rmdir()


def make_git_repo(root):
    """Create three commits and offline refs in a disposable checkout."""
    root = Path(root)
    init_git(root)

    make_wiki(root, {"demo.md": "# Demo topic\n"}, "# Topics\n- [Demo topic](demo.md)\n")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "A: create the wiki")
    base = git(root, "rev-parse", "HEAD")

    proof = root / "docs" / "evidence" / "README"
    proof.parent.mkdir(parents=True)
    proof.write_text("Release review evidence\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "B: record reviewed release evidence")
    release = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0", release)
    git(root, "update-ref", "refs/remotes/origin/main", release)

    git(root, "checkout", "--quiet", "-b", "feature-x")
    proof.write_text("Release review evidence\nFeature review evidence\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "C: add feature evidence")
    feature = git(root, "rev-parse", "HEAD")
    git(root, "branch", "feature/nested", feature)
    git(root, "checkout", "--quiet", "main")
    return base, release, feature


def write_config(root, **config):
    (Path(root) / ".repo-waymark.json").write_text(json.dumps(config), encoding="utf-8")


def sync_fixture(d):
    status.sync(d, [])


class StatusBlockFixture(unittest.TestCase):
    def setUp(self):
        self.root = fixture()
        self.addCleanup(shutil.rmtree, self.root)
        self.mem = Path(self.root) / WIKI_DIR
        self.page = os.path.join(self.mem, "demo.md")
        self.index = os.path.join(self.mem, "index-a.md")
        run = self.run_tool("status.py", "sync")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def run_tool(self, script, *args, **overrides):
        return run_tool(script, *args, cwd=self.root, **{"WAYMARK_ROOT": self.root, **overrides})
