import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import wl


@unittest.skipUnless(os.name == "posix", "uses an executable Git fixture")
class GitSubprocess(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {
            "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "GIT_TERMINAL_PROMPT": "1", "GIT_NO_LAZY_FETCH": "0", "GIT_OPTIONAL_LOCKS": "1",
        })
        env.start()
        self.addCleanup(env.stop)

    def install_git(self, commands):
        path = self.root / "git"
        path.write_text("#!/bin/sh\nshift 2\n" + commands, encoding="utf-8")
        path.chmod(0o700)

    def test_each_git_command_runs_noninteractively_without_fetches_or_locks(self):
        self.install_git("""if [ "$GIT_TERMINAL_PROMPT:$GIT_NO_LAZY_FETCH:$GIT_OPTIONAL_LOCKS" != 0:1:0 ]; then
    exit 1
fi
case "$1" in
    rev-parse) printf '.git\\n' ;;
    cat-file) read -r spec; printf '0123456789abcdef0123456789abcdef01234567\\n' ;;
esac
""")
        git = wl.Git(self.root)
        self.assertTrue(git.ok)
        self.assertEqual(git.commit("HEAD"), "0123456789abcdef0123456789abcdef01234567")

    def test_git_timeouts_are_reported_as_unavailable(self):
        self.install_git("""case "$*" in
    'rev-parse --git-dir') printf '.git\\n' ;;
    *) exec sleep 1 ;;
esac
""")
        git = wl.Git(self.root)
        self.assertTrue(git.ok)
        with patch.object(wl, "GIT_TIMEOUT", 0.25, create=True):
            with self.assertRaises(wl.GitUnavailable):
                git.commit("HEAD")
            with self.assertRaises(wl.GitUnavailable):
                git.tags()

    def test_initial_git_timeout_marks_git_unavailable(self):
        self.install_git("exec sleep 1\n")
        with patch.object(wl, "GIT_TIMEOUT", 0.25, create=True):
            self.assertFalse(wl.Git(self.root).ok)
