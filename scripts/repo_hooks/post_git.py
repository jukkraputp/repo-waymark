#!/usr/bin/env python3
"""Claude Code PostToolUse hook (matcher `Bash|PowerShell`): remind Claude to update the wiki.

Reads the hook input JSON from stdin. When `tool_input.command` may have run `git commit`,
`git merge`, `git push`, `gh pr create` or `gh pr merge` in a repository with a Repo Waymark wiki,
prints PostToolUse JSON whose `hookSpecificOutput.additionalContext` asks Claude to update the
matching topic page. Otherwise prints nothing. The hook cannot see whether the command succeeded,
so the reminder says the status *may* have changed.

Matching is deliberately simple. The command is split into words with shell quoting rules and cut
at `;`, `&&`, `||`, `|`, `&`, parentheses and newlines; if the text cannot be split that way (for
example PowerShell quoting), it falls back to a whitespace split. A segment matches when its first
word, after `VAR=value` assignments and the prefixes `command`, `sudo`, `env`, `time` and `nohup`,
is `git` or `gh`:

- git: global options such as `-C <dir>` and `-c <k=v>` are skipped. A `-C` directory that
  resolves outside the wiki's repository (relative to the payload `cwd`) does not match.
  `git push --dry-run`/`-n` and `git commit --dry-run` do not match.
- gh: flags such as `-R/--repo <owner/repo>` may appear before or after `pr`.

A match after `||` only runs if an earlier command failed; it still reminds. Text inside quotes
("echo 'git commit'") never matches. Commands nested in `bash -c "..."`, `eval`, backticks or
scripts are not seen; `$(...)` is seen as its own command. A `cd` into another repository before
the git command is not tracked.

Never raises and always exits 0.
"""
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)
try:
    import wikilib as wl
except Exception:  # a broken helper must never break a tool call
    wl = None

GIT_SUBCOMMANDS = {"commit", "merge", "push"}
GH_PR_SUBCOMMANDS = {"create", "merge"}
SEPARATOR_CHARS = set(";&|()\n")
FALLBACK_SEPARATORS = re.compile(r"[;&|()\n]+")
# Prefix commands and the options of theirs that take a separate value.
PREFIXES = {
    "command": set(),
    "sudo": {"-u", "-g", "-U", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-R"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "time": {"-f", "-o", "--format", "--output"},
    "nohup": set(),
}
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
                     "--super-prefix", "--exec-path"}
GH_VALUE_FLAGS = {"-R", "--repo"}


def _split_fallback(command):
    for part in FALLBACK_SEPARATORS.split(command):
        words = part.split()
        if words:
            yield words


def _segments(command):
    lexer = shlex.shlex(command.replace("\r", " ").replace("\n", " ; "), posix=True,
                        punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes or PowerShell escapes
        yield from _split_fallback(command)
        return
    segment = []
    for token in tokens:
        if token and set(token) <= SEPARATOR_CHARS:
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _is_assignment(word):
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def _program_start(words):
    """Index of the real program word after assignments and prefix commands."""
    i = 0
    while i < len(words):
        if _is_assignment(words[i]):
            i += 1
            continue
        prefix = _program_name(words[i])
        if prefix not in PREFIXES:
            return i
        takes_value = PREFIXES[prefix]
        i += 1
        while i < len(words):
            word = words[i]
            if word == "--":
                i += 1
                break
            if prefix == "env" and _is_assignment(word):
                i += 1
            elif word.startswith("-") and len(word) > 1:
                i += 2 if word in takes_value else 1
            else:
                break
    return i


def _program_name(word):
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _resolve(base, path):
    return os.path.realpath(os.path.join(base, os.path.expanduser(path)))


def _inside(path, root):
    try:
        return os.path.commonpath([path, os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:  # different drives
        return False


def _short_flag(word, flag, value_flags):
    """Whether a bundled short-option word such as `-fn` sets `flag` before any value option."""
    if not word.startswith("-") or word.startswith("--") or len(word) < 2:
        return False
    for c in word[1:]:
        if c == flag:
            return True
        if c in value_flags or not c.isalnum():
            return False
    return False


def _git_match(rest, cwd, root):
    j = 0
    target = None
    unresolved = False
    while j < len(rest) and rest[j].startswith("-"):
        opt = rest[j]
        if opt in GIT_VALUE_OPTIONS:
            if opt == "-C" and j + 1 < len(rest) and rest[j + 1]:
                value = rest[j + 1]
                unresolved = unresolved or "$" in value or "%" in value
                target = _resolve(target or cwd or root or os.getcwd(), value)
            j += 2
        else:
            j += 1
    if j >= len(rest) or rest[j] not in GIT_SUBCOMMANDS:
        return None
    sub, args = rest[j], rest[j + 1:]
    if target is not None and root and not unresolved and not _inside(target, root):
        return None
    if "--" in args:
        args = args[:args.index("--")]
    if "--dry-run" in args:
        return None
    if sub == "push" and any(_short_flag(a, "n", "o") for a in args):
        return None
    return "git " + sub


def _gh_match(rest):
    positional = []
    j = 0
    while j < len(rest) and len(positional) < 2:
        word = rest[j]
        if word in GH_VALUE_FLAGS:
            j += 2
        elif word.startswith("-"):
            j += 1
        else:
            positional.append(word)
            j += 1
    if len(positional) == 2 and positional[0] == "pr" and positional[1] in GH_PR_SUBCOMMANDS:
        return "gh pr " + positional[1]
    return None


def _match(words, cwd, root):
    i = _program_start(words)
    if i >= len(words):
        return None
    prog = _program_name(words[i])
    rest = words[i + 1:]
    if prog == "git":
        return _git_match(rest, cwd, root)
    if prog == "gh":
        return _gh_match(rest)
    return None


def matched_command(command, cwd=None, root=None):
    """The status-changing command `command` may run, such as "git push", or None."""
    if not isinstance(command, str) or not command.strip():
        return None
    for words in _segments(command):
        hit = _match(words, cwd, root)
        if hit:
            return hit
    return None


def tool_command(root, script):
    """`python3 <path>` for a sibling script, relative to the repo root when it lies inside it."""
    path = os.path.join(SCRIPTS, script)
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:
        rel = None
    if rel is None or rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        rel = path
    return "python3 " + shlex.quote(rel)


def reminder(hit, root, cfg):
    status = tool_command(root, "status.py")
    label = cfg.get("merged_label", "main")
    return (
        "Repo wiki reminder: this command may have run `%s`, which can change a feature's status "
        "(code committed, PR opened, merged to `%s`, tested, released). If it did, update that "
        "topic's `## Current status` block in `%s/` (or create a page from `%s template` and add "
        "one line to the matching `index-*.md`). Keep the evidence: backticked commit SHAs, branch "
        "and PR refs. `Merged: %s` means reachable from `%s`. Then run `%s sync <page>.md`. "
        "Write `unknown` rather than guess. If the command failed or no feature's status changed, "
        "ignore this."
        % (hit, label, cfg["wiki_dir"].replace(os.sep, "/"), status, label, cfg["main_ref"], status)
    )


def hook_root(cwd):
    try:
        return wl.find_hook_root(cwd)
    except wl.ConfigError:
        return None


def main():
    try:
        if wl is None:
            return 0
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        cwd = payload.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None
        if not isinstance(command, str) or not command.strip():
            return 0
        root = hook_root(cwd)
        if root is None:
            return 0
        hit = matched_command(command, cwd=cwd, root=root)
        if hit:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": reminder(hit, root, wl.config_for(root)),
            }}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
