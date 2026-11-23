#!/usr/bin/env python3
"""Claude Code Stop hook: block the stop once when this checkout's wiki edits left it out of sync.

Reads the hook input JSON from stdin. If `stop_hook_active` is true, exits silently (no loops).
Otherwise finds the wiki root from the payload `cwd` (which follows Claude into a worktree),
falling back to CLAUDE_PROJECT_DIR and the process directory, and runs `status.py check` (resolved
next to this hook's directory) against it.

Blocks only when both hold:
- the check prints its summary line `STATUS CHECK pages=N problems=M` with M > 0, and
- at least one problem involves a wiki file with uncommitted changes in this checkout
  (`git status --porcelain -- <wiki_dir>`).

Drift that is already committed (for example someone else's) is not this session's to fix, so it
stays silent, as do: an in-sync wiki, no wiki, a crash, traceback or timeout, a missing summary
line, and Git being unavailable. Never raises and always exits 0.
"""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
STATUS = os.path.join(SCRIPTS, "status.py")
sys.path.insert(0, SCRIPTS)
try:
    import wikilib as wl
except Exception:  # a broken helper must never block a stop
    wl = None

CHECK_TIMEOUT_S = 10
GIT_TIMEOUT_S = 4
MAX_DETAIL = 1500
SUMMARY = re.compile(r"^STATUS CHECK pages=(\d+) problems=(\d+)\s*$", re.M)
PROBLEM_START = re.compile(r"^(?:[^\s:]+\.md: |cannot render)")
PAGE = re.compile(r"^([^\s:]+\.md): ")
INDEX_FILE = re.compile(r"index line ([^\s:]+\.md):\d+")
ANY_INDEX = re.compile(r"expected exactly one index line")
SYNCABLE = re.compile(r"differs from .*?\. Run `")


def split_problems(out):
    """Group check output into problem blocks (first line + continuation/diff lines)."""
    blocks = []
    for line in out.splitlines():
        if SUMMARY.match(line):
            continue
        if PROBLEM_START.match(line) or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return ["\n".join(b) for b in blocks]


def dirty_wiki_files(root, wiki_rel):
    """Basenames of files under the wiki directory with uncommitted changes, or None without Git."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "-z", "--untracked-files=all",
             "--", wiki_rel],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_S, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    names = set()
    entries = proc.stdout.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        names.add(os.path.basename(entry[3:].rstrip("/")))
        if entry[0] in "RC":   # a rename or copy is followed by its source path
            if i < len(entries) and entries[i]:
                names.add(os.path.basename(entries[i].rstrip("/")))
            i += 1
    return names


def involves(block, dirty):
    """Whether a problem block concerns any of the dirty wiki files."""
    first = block.split("\n", 1)[0]
    page = PAGE.match(first)
    if page is None:   # not attributable to one page: any uncommitted wiki edit counts
        return bool(dirty)
    if page.group(1) in dirty:
        return True
    index = INDEX_FILE.search(first)
    if index and index.group(1) in dirty:
        return True
    return bool(ANY_INDEX.search(first)) and any(n.startswith("index-") for n in dirty)


def tool_command(root):
    """`python3 <status.py>`, relative to the repo root when the tool lies inside it."""
    try:
        rel = os.path.relpath(os.path.realpath(STATUS), os.path.realpath(root))
    except ValueError:
        rel = None
    if rel is None or rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        rel = STATUS
    return "python3 " + shlex.quote(rel)


def build_reason(blocks, skipped, root, wiki_rel):
    status = tool_command(root)
    fixable, manual = [], []
    for b in blocks:
        (fixable if SYNCABLE.search(b.split("\n", 1)[0]) else manual).append(b)
    parts = ["The repo wiki (%s/) has uncommitted edits that fail `%s check`." % (wiki_rel, status)]
    if fixable:
        pages = sorted(set(m.group(1) for m in (PAGE.match(b) for b in fixable) if m))
        parts.append("Index drift that `sync` can fix%s: run `%s sync%s` "
                     "(do not hand-edit generated index lines)."
                     % (" in " + ", ".join(pages) if pages else "", status,
                        " " + " ".join(pages) if pages else ""))
    if manual:
        parts.append("Problems `sync` cannot fix (a page lacks its single index line, has an "
                     "invalid status block, or cannot be rendered): edit the page or its index "
                     "file by hand.")
    parts.append("Then re-run `%s check`." % status)
    if skipped:
        parts.append("%d other problem(s) involve only committed wiki files and are not this "
                     "change's to fix." % skipped)
    detail = "\n".join(blocks).strip()
    if len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL - 1] + "…"
    # Wiki text is untrusted: keep it from imitating harness tags.
    detail = detail.replace("<", "&lt;").replace(">", "&gt;")
    return " ".join(parts) + "\n" + detail


def main():
    try:
        if wl is None:
            return 0
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("stop_hook_active"):
            return 0
        try:
            root = wl.find_hook_root(payload.get("cwd"))
            cfg = wl.config_for(root)
        except wl.ConfigError:
            return 0
        proc = subprocess.run(
            [sys.executable or "python3", STATUS, "--root", root, "check"],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=CHECK_TIMEOUT_S, universal_newlines=True,
        )
        out = proc.stdout or ""
        m = SUMMARY.search(out)
        if not m or int(m.group(2)) <= 0:
            return 0
        wiki_rel = cfg["wiki_dir"].replace(os.sep, "/")
        dirty = dirty_wiki_files(root, cfg["wiki_dir"])
        if not dirty:
            return 0
        blocks = split_problems(out)
        mine = [b for b in blocks if involves(b, dirty)]
        if not mine:
            return 0
        print(json.dumps({"decision": "block",
                          "reason": build_reason(mine, len(blocks) - len(mine), root, wiki_rel)}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
