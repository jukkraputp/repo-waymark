"""The vendorable project hooks: the post-git reminder and the Stop-time wiki check."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import (PAGE, SCRIPTS, WIKI_DIR, fixture, git, make_git_repo, run_tool,
                     subprocess_env, write_config)

HOOKS = SCRIPTS / "repo_hooks"
sys.path.insert(0, str(HOOKS))
import post_git  # noqa: E402

OUT_OF_SYNC = "- [Demo topic](demo.md) — stale hand-written line\n"


def run_hook(script, payload, cwd, **env):
    return subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
        cwd=cwd, env=subprocess_env(**env),
    )


def reminder_of(run):
    if not run.stdout.strip():
        return None
    out = json.loads(run.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    return out["additionalContext"]


class PostGitMatching(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        os.mkdir(os.path.join(self.root, "sub"))

    def hit(self, command):
        return post_git.matched_command(command, cwd=self.root, root=self.root)

    def test_reminds_for_status_changing_commands(self):
        cases = {
            "git commit -m x": "git commit",
            "gh pr create --fill": "gh pr create",
            "cd a && git push": "git push",
            "git push origin HEAD; Write-Host done": "git push",
            "command git commit -m x": "git commit",
            "gh -R org/repo pr merge 1": "gh pr merge",
            "gh --repo=org/repo pr create": "gh pr create",
            "gh pr merge -R org/repo 1": "gh pr merge",
            "sudo -u builder git push": "git push",
            "env GIT_TRACE=1 git merge topic": "git merge",
            "time nohup git push": "git push",
            "true || git push": "git push",
            "git -C sub push": "git push",
            "git.exe push origin main": "git push",
            "git push -f": "git push",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(self.hit(command), expected)

    def test_ignores_commands_that_change_nothing(self):
        for command in ("git push --dry-run", "git push -n origin main", "git push -fn",
                        "git commit --dry-run -m x", "git -C ../other-repo push",
                        "echo 'git commit'", "git status", "gh pr view 1", "git log --merges"):
            with self.subTest(command=command):
                self.assertIsNone(self.hit(command))

    def test_c_option_resolves_against_payload_cwd(self):
        inner = os.path.join(self.root, "sub")
        self.assertEqual(post_git.matched_command("git -C .. push", cwd=inner, root=self.root),
                         "git push")
        self.assertIsNone(post_git.matched_command("git -C ../.. push", cwd=inner, root=self.root))
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        self.assertIsNone(self.hit("git -C %s commit -m x" % outside))

    def test_powershell_text_that_shlex_rejects_falls_back_to_words(self):
        self.assertEqual(self.hit("git commit -m \"it`\"s\"; git push"), "git commit")
        self.assertEqual(self.hit("Write-Host 'unbalanced ; git push"), "git push")


class PostGitHook(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(fixture())
        self.addCleanup(shutil.rmtree, self.root)

    def payload(self, command, tool="Bash", cwd=None):
        return {"hook_event_name": "PostToolUse", "tool_name": tool,
                "tool_input": {"command": command}, "cwd": cwd or self.root}

    def test_reminder_says_may_and_uses_configured_wording(self):
        write_config(self.root, main_ref="origin/develop", merged_label="develop")
        run = run_hook("post_git.py", self.payload("true || git push"), self.root)
        self.assertEqual(run.returncode, 0, run.stderr)
        text = reminder_of(run)
        self.assertIn("may have run `git push`", text)
        self.assertIn("merged to `develop`", text)
        self.assertIn("`origin/develop`", text)
        self.assertIn("`.claude/memory/`", text)
        self.assertNotIn("`git push` ran", text)

    def test_wiki_dir_and_tool_path_come_from_the_layout(self):
        shutil.move(os.path.join(self.root, ".claude"), os.path.join(self.root, "notes-tmp"))
        os.makedirs(os.path.join(self.root, "docs"))
        shutil.move(os.path.join(self.root, "notes-tmp", "memory"), os.path.join(self.root, "docs", "wiki"))
        write_config(self.root, wiki_dir="docs/wiki")
        vendored = os.path.join(self.root, "tools", "waymark")
        shutil.copytree(SCRIPTS, vendored, ignore=shutil.ignore_patterns("__pycache__"))
        run = subprocess.run(
            [sys.executable, os.path.join(vendored, "repo_hooks", "post_git.py")],
            input=json.dumps(self.payload("git commit -m x")), capture_output=True, text=True,
            cwd=self.root, env=subprocess_env())
        text = reminder_of(run)
        self.assertIn("`docs/wiki/`", text)
        self.assertIn("`python3 tools/waymark/status.py sync <page>.md`", text)
        self.assertNotIn(self.root, text)

    def test_tool_outside_the_repo_prints_its_absolute_path(self):
        text = reminder_of(run_hook("post_git.py", self.payload("git commit -m x"), self.root))
        self.assertIn(str(SCRIPTS / "status.py"), text)

    def test_powershell_tool_payload(self):
        payload = self.payload("git push origin HEAD; Write-Host done", tool="PowerShell")
        self.assertIn("`git push`", reminder_of(run_hook("post_git.py", payload, self.root)))

    def test_silent_without_a_match_a_wiki_or_valid_input(self):
        self.assertIsNone(reminder_of(run_hook("post_git.py", self.payload("git status"), self.root)))
        with tempfile.TemporaryDirectory() as empty:
            run = run_hook("post_git.py", self.payload("git push", cwd=empty), empty)
            self.assertEqual((run.returncode, run.stdout), (0, ""))
        run = subprocess.run([sys.executable, str(HOOKS / "post_git.py")], input="not json",
                             capture_output=True, text=True, env=subprocess_env())
        self.assertEqual((run.returncode, run.stdout), (0, ""))


class StopCheck(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        make_git_repo(self.root)
        self.mem = Path(self.root) / WIKI_DIR
        (self.mem / "demo.md").write_text(PAGE, encoding="utf-8")
        run = run_tool("status.py", "--root", self.root, "sync", cwd=self.root)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        git(self.root, "add", ".")
        git(self.root, "commit", "--quiet", "-m", "D: synced wiki")

    def stop(self, payload=None, cwd=None, **env):
        payload = {"hook_event_name": "Stop", "cwd": self.root} if payload is None else payload
        run = run_hook("stop_check.py", payload, cwd or self.root, **env)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout) if run.stdout.strip() else None

    def desync_index(self):
        index = self.mem / "index-a.md"
        lines = index.read_text(encoding="utf-8").splitlines(True)
        index.write_text("".join(OUT_OF_SYNC if "(demo.md)" in l else l for l in lines),
                         encoding="utf-8")

    def test_in_sync_is_silent(self):
        self.assertIsNone(self.stop())

    def test_uncommitted_desync_blocks_with_sync_wording(self):
        self.desync_index()
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("sync demo.md", out["reason"])
        self.assertIn("do not hand-edit generated index lines", out["reason"])
        self.assertLessEqual(len(out["reason"]), 2500)

    def test_stop_hook_active_is_silent(self):
        self.desync_index()
        self.assertIsNone(self.stop({"cwd": self.root, "stop_hook_active": True}))

    def test_committed_desync_is_silent(self):
        self.desync_index()
        git(self.root, "commit", "--quiet", "-am", "E: someone else's drift")
        self.assertIsNone(self.stop())

    def test_unrelated_uncommitted_change_does_not_block_committed_drift(self):
        self.desync_index()
        git(self.root, "commit", "--quiet", "-am", "E: someone else's drift")
        (self.mem / "notes.md").write_text("# Notes\n", encoding="utf-8")
        self.assertIsNone(self.stop())

    def test_manual_fix_wording(self):
        index = self.mem / "index-a.md"
        index.write_text("".join(l for l in index.read_text(encoding="utf-8").splitlines(True)
                                 if "(demo.md)" not in l), encoding="utf-8")
        out = self.stop()
        self.assertIn("edit the page or its index file by hand", out["reason"])
        self.assertNotIn("sync demo.md", out["reason"])

    def test_status_crash_is_silent(self):
        self.desync_index()
        vendored = os.path.join(self.root, "tool")
        shutil.copytree(SCRIPTS, vendored, ignore=shutil.ignore_patterns("__pycache__"))
        Path(vendored, "status.py").write_text("raise SystemExit('boom')\n", encoding="utf-8")
        run = subprocess.run(
            [sys.executable, os.path.join(vendored, "repo_hooks", "stop_check.py")],
            input=json.dumps({"cwd": self.root}), capture_output=True, text=True,
            env=subprocess_env())
        self.assertEqual((run.returncode, run.stdout), (0, ""))

    def test_without_git_is_silent(self):
        self.desync_index()
        empty_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_path)
        self.assertIsNone(self.stop(PATH=empty_path))

    def test_reason_names_vendored_tool_relative_to_root(self):
        vendored = os.path.join(self.root, "tool", "waymark")
        shutil.copytree(SCRIPTS, vendored, ignore=shutil.ignore_patterns("__pycache__"))
        self.desync_index()
        run = subprocess.run(
            [sys.executable, os.path.join(vendored, "repo_hooks", "stop_check.py")],
            input=json.dumps({"cwd": self.root}), capture_output=True, text=True,
            env=subprocess_env())
        reason = json.loads(run.stdout)["reason"]
        self.assertIn("`python3 tool/waymark/status.py sync demo.md`", reason)


class PromptHookRoot(unittest.TestCase):
    def test_payload_cwd_wins_over_project_dir(self):
        project, worktree = fixture(), fixture()
        for root in (project, worktree):
            self.addCleanup(shutil.rmtree, root)
        project_index = Path(project) / WIKI_DIR / "index-a.md"
        project_index.write_text(project_index.read_text(encoding="utf-8")
                                 + "- [Project only](project.md) — zebracorn note\n", encoding="utf-8")
        worktree_index = Path(worktree) / WIKI_DIR / "index-a.md"
        worktree_index.write_text(worktree_index.read_text(encoding="utf-8")
                                  + "- [Worktree only](wt.md) — zebracorn note\n", encoding="utf-8")
        payload = json.dumps({"prompt": "zebracorn", "cwd": worktree})
        env = {"CLAUDE_PROJECT_DIR": project, "WAYMARK_HOOK_MIN_KEYWORDS": 1}
        run = run_tool("prompt_hook.py", cwd=project, input_text=payload, **env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Worktree only", run.stdout)
        self.assertNotIn("Project only", run.stdout)
        # A cwd without a wiki falls back to the project, and WAYMARK_ROOT still wins.
        with tempfile.TemporaryDirectory() as empty:
            run = run_tool("prompt_hook.py", cwd=project, **env,
                           input_text=json.dumps({"prompt": "zebracorn", "cwd": empty}))
            self.assertIn("Project only", run.stdout)
        run = run_tool("prompt_hook.py", cwd=project, input_text=payload, WAYMARK_ROOT=project, **env)
        self.assertIn("Project only", run.stdout)


if __name__ == "__main__":
    unittest.main()
