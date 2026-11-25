#!/usr/bin/env python3
"""Draft a "Where to look" map for a topic page. Prints text; never edits a file.

    python3 scripts/map.py template
    python3 scripts/map.py [--root DIR] suggest [--ref REF] [--max N] TERM...
    python3 scripts/map.py [--root DIR] suggest [--ref REF] [--max N] --commits SHA...
    python3 scripts/map.py [--root DIR] suggest [--max N] --branch REF

`template` prints an empty map section to paste at the end of a page (after `<!-- history -->` on a
status page). `suggest` reads the selected Git root and prints draft bullets for a person to trim.

With TERMs it finds commits on `main_ref` (or --ref, used for both the history and checking that
files exist) whose message contains any TERM (fixed string, case-insensitive; merge commits count).
`--commits` uses the given commits instead of a search. Each commit is diffed against its first
parent (a root commit against the empty tree). Files are ranked by how many of those commits touch
them, then by lines changed. Files that are not regular files at the ref, and files in the wiki
directory, are dropped. Up to --max (default 8) bullets are printed with a `TODO` role, then a line
listing the commits used.

`--branch REF` instead lists the files REF changes against its merge base with `main_ref`, written
as `REF:path` pointers.

Without TERMs, --commits or --branch, `suggest` is a usage error. Exit 0 with suggestions, 1 when
nothing matches, 2 on usage, configuration or Git errors.
"""
import argparse
import os
import posixpath
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402
import wikilib as wl  # noqa: E402

MAX_COMMITS = 200

TEMPLATE = """%s
%s

- `path/to/entry_point` `Symbol.name` — what to read it for
- Trap: `path/to/old_file` — why not; use `path/to/new_file`
""" % (maplib.MAP_MARKER, maplib.MAP_HEADING)


def empty_tree(git):
    rc, out = git.run("hash-object", "-t", "tree", os.devnull)
    return out.strip() if rc == 0 and out.strip() else None


def run_bytes(git, *args):
    """(returncode, stdout bytes) of a bounded read-only Git call; paths stay undecoded."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    try:
        r = subprocess.run(["git", "-C", git.root, *args], env=env, timeout=30, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise wl.GitUnavailable("git %s is unavailable: %s" % (args[0], exc)) from exc
    return r.returncode, r.stdout


def _decode(raw):
    return raw.decode("utf-8", "replace")


def parse_numstat(raw):
    """[(path, lines)] from `git diff --numstat -z -M` output; a rename counts its new path."""
    tokens = raw.split(b"\0")
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        head = tok.split(b"\t", 2)
        if len(head) != 3:
            continue
        added, deleted, path = head
        if not path:                      # rename or copy: old and new paths follow as tokens
            if i + 1 >= len(tokens):
                break
            path = tokens[i + 1]
            i += 2
        lines = int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else 0
        out.append((_decode(path), lines))
    return out


def search_commits(git, tip, terms):
    seen, ordered = set(), []
    for term in terms:
        rc, out = run_bytes(git, "log", "--fixed-strings", "-i", "--grep=" + term,
                            "--format=%H", "-n", str(MAX_COMMITS), tip)
        if rc != 0:
            continue
        for sha in _decode(out).split():
            if sha not in seen:
                seen.add(sha)
                ordered.append(sha)
    return ordered[:MAX_COMMITS]


def commit_files(git, sha, tree):
    """[(path, lines)] a commit changes against its first parent (or the empty tree)."""
    rc, out = run_bytes(git, "rev-parse", "--verify", "-q", sha + "^1")
    parent = _decode(out).strip() if rc == 0 and out.strip() else tree
    if parent is None:
        return []
    rc, out = run_bytes(git, "diff", "--numstat", "-z", "-M", "--relative", parent, sha, "--", ".")
    return parse_numstat(out) if rc == 0 else []


def in_wiki(path, cfg):
    wiki = posixpath.normpath(cfg["wiki_dir"]).strip("/")
    return path == wiki or path.startswith(wiki + "/")


def subject(git, sha):
    rc, out = run_bytes(git, "log", "-1", "--format=%h %s", sha)
    return _decode(out).strip()[:100] if rc == 0 else sha[:7]


def branch_candidates(git, base, ref):
    """Counter of paths a branch changes against its merge base with `base`."""
    counts = Counter()
    rc, _ = git.run("merge-base", base, ref)
    if rc == 0:
        rc, out = git.run("diff", "--relative", "--name-only", "--diff-filter=AMR",
                          "%s...%s" % (base, ref), "--", ".")
    else:
        tree = empty_tree(git)
        if tree is None:
            return counts
        rc, out = git.run("diff", "--relative", "--name-only", "--diff-filter=AMR", tree, ref, "--", ".")
    if rc == 0:
        counts.update(l for l in out.split("\n") if l.strip())
    return counts


def emit(chosen, prefix=""):
    print(maplib.MAP_MARKER)
    print(maplib.MAP_HEADING)
    print()
    for path in chosen:
        print("- `%s%s` — TODO: say what to read it for" % (prefix, path))


def suggest(root, cfg, opts):
    git = wl.Git(wl.git_root_for(root))
    if not git.ok:
        print("map.py suggest needs a Git checkout", file=sys.stderr)
        return 2
    base = cfg["main_ref"]
    if opts.branch:
        branch = git.commit(opts.branch)
        if branch is None or git.commit(base) is None:
            print("`%s` or `%s` does not resolve" % (opts.branch, base), file=sys.stderr)
            return 2
        counts = branch_candidates(git, base, opts.branch)
        paths = [p for p, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        chosen = [p for p in paths if not in_wiki(p, cfg) and "`" not in p and "\n" not in p
                  and maplib.blob_at(git, branch, git.project_path(p))][:opts.max]
        if not chosen:
            print("no candidate files found", file=sys.stderr)
            return 1
        emit(chosen, opts.branch + ":")
        return 0
    ref = opts.ref or base
    tip = git.commit(ref)
    if tip is None:
        print("`%s` does not resolve" % ref, file=sys.stderr)
        return 2
    if opts.commits:
        shas = []
        for given in opts.commits:
            sha = git.commit(given)
            if sha is None:
                print("commit `%s` does not resolve" % given, file=sys.stderr)
                return 2
            if sha not in shas:
                shas.append(sha)
    else:
        shas = search_commits(git, tip, opts.terms)
    tree = empty_tree(git)
    touched, lines = Counter(), Counter()
    for sha in shas:
        merged = {}
        for path, n in commit_files(git, sha, tree):
            merged[path] = merged.get(path, 0) + n
        for path, n in merged.items():
            touched[path] += 1
            lines[path] += n
    ranked = sorted(touched, key=lambda p: (-touched[p], -lines[p], p))
    chosen = [p for p in ranked if not in_wiki(p, cfg) and "`" not in p and "\n" not in p
              and maplib.blob_at(git, tip, git.project_path(p))][:opts.max]
    if not chosen:
        print("no candidate files found", file=sys.stderr)
        return 1
    emit(chosen)
    print()
    used = shas[:10]
    print("Commits used (%d%s): %s" % (len(shas), ", first 10 shown" if len(shas) > 10 else "",
                                       "; ".join(subject(git, sha) for sha in used)))
    return 0


def main(argv):
    try:
        explicit_root, args = wl.split_root_arg(argv)
    except wl.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("template")
    s = sub.add_parser("suggest")
    s.add_argument("--ref")
    s.add_argument("--branch")
    s.add_argument("--commits", nargs="+", metavar="SHA")
    s.add_argument("--max", type=int, default=8)
    s.add_argument("terms", nargs="*", metavar="TERM")
    opts = parser.parse_args(args)
    if opts.cmd == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if not 1 <= opts.max <= maplib.MAX_ENTRIES:
        parser.error("--max must be 1 to %d" % maplib.MAX_ENTRIES)
    chosen_modes = [bool(opts.terms), bool(opts.commits), bool(opts.branch)]
    if sum(chosen_modes) != 1:
        parser.error("give TERMs, --commits SHA..., or --branch REF (exactly one)")
    if opts.branch and opts.ref:
        parser.error("--ref does not apply to --branch")
    try:
        try:
            root = wl.find_root(explicit_root)
        except wl.ConfigError:
            if explicit_root is not None:
                raise
            root = os.getcwd()
        cfg = wl.config_for(root)
    except wl.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        return suggest(root, cfg, opts)
    except wl.GitUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
