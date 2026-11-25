#!/usr/bin/env python3
"""Draft a "Where to look" map for a topic page. Prints text; never edits a file.

    python3 scripts/map.py template
    python3 scripts/map.py [--root DIR] suggest [--ref REF] [--max N] [--] TERM...
    python3 scripts/map.py [--root DIR] suggest [--ref REF] [--max N] --commit SHA [--commit SHA ...]
    python3 scripts/map.py [--root DIR] suggest [--max N] --branch REF

`template` prints a page skeleton (H1, About line, the map section) and the index line to add.
`suggest` reads the selected Git root and prints draft bullets for a person to trim.

With TERMs it finds commits on `main_ref` (or --ref, used for both the history and checking that
files exist) whose message contains any TERM (fixed string, case-insensitive; merge commits count).
One `git log` call ORs all terms and returns at most 200 commits in total. `--commit` (repeatable,
each value must resolve to a commit) uses the given commits instead of a search. Each commit is
diffed against its first parent (a root commit against the empty tree; a commit at a shallow-clone
boundary has unknown history and is skipped with a note). Files are ranked by how many of those
commits touch them, then by lines changed. Files that are not regular files at the ref, and files in
the wiki directory, are dropped. Up to --max (default 8) bullets are printed with a `TODO` role, then
a line listing the commits used. When --ref names a different commit than `main_ref`, pointers are
written `REF:path`, because a plain pointer is read at `main_ref`.

`--branch REF` instead lists the files REF changes against its merge base with `main_ref`, written
as `REF:path` pointers. Put `--` before TERMs that start with `-`.

Without exactly one of TERMs, --commit or --branch, `suggest` is a usage error. Exit 0 with
suggestions, 1 when nothing matches, 2 on usage, configuration or Git errors.
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

TEMPLATE = """# Page title

One-line About of this topic.

%s
%s

- `path/to/entry_point` `Symbol.name` — what to read it for
- Trap: `path/to/old_file` — why not; use `path/to/new_file`

Index line to add to an index-*.md file (not part of the page):
- [Page title](page.md) — About of this topic. · Keywords: term, alias
""" % (maplib.MAP_MARKER, maplib.MAP_HEADING)

GIT_CONFIG = ("-c", "diff.renames=true", "-c", "core.quotepath=off")


class GitFailure(Exception):
    pass


def run_bytes(git, *args, ok=(0,)):
    """Stdout bytes of a bounded read-only Git call. Statuses outside `ok` raise GitFailure."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    try:
        r = subprocess.run(["git", *GIT_CONFIG, "-C", git.root, *args], env=env, timeout=30,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitFailure("git %s is unavailable: %s" % (args[0], exc)) from exc
    if r.returncode not in ok:
        raise GitFailure("git %s failed (exit %d): %s" % (args[0], r.returncode,
                                                         r.stderr.decode("utf-8", "replace").strip()[:200]))
    return r.stdout


def _decode(raw):
    return raw.decode("utf-8", "replace")


def empty_tree(git):
    out = _decode(run_bytes(git, "hash-object", "-t", "tree", os.devnull)).strip()
    if not out:
        raise GitFailure("git could not produce the empty tree")
    return out


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


def parse_names(raw):
    return [_decode(t) for t in raw.split(b"\0") if t]


def search_commits(git, tip, terms):
    """Commits reachable from `tip` whose message contains any term: one `git log`, 200 at most."""
    args = ["log", "--fixed-strings", "-i", "--format=%H", "-n", str(MAX_COMMITS)]
    args += ["--grep=" + t for t in terms]
    return _decode(run_bytes(git, *args, tip, "--")).split()


def shallow_boundaries(git):
    path = _decode(run_bytes(git, "rev-parse", "--git-path", "shallow")).strip()
    path = path if os.path.isabs(path) else os.path.join(git.root, path)
    try:
        with open(path, encoding="utf-8") as handle:
            return set(handle.read().split())
    except OSError:
        return set()


def commit_files(git, sha, tree, boundaries):
    """[(path, lines)] a commit changes against its first parent (or the empty tree); None when its
    history is unknown (a shallow-clone boundary)."""
    fields = _decode(run_bytes(git, "rev-list", "--parents", "-n", "1", sha)).split()
    parents = fields[1:]
    if not parents:
        if sha in boundaries:
            return None
        parent = tree
    else:
        parent = parents[0]
    raw = run_bytes(git, "diff", "--numstat", "-z", "-M", "--relative", parent, sha, "--", ".")
    return parse_numstat(raw)


def in_wiki(path, cfg):
    wiki = posixpath.normpath(cfg["wiki_dir"]).strip("/")
    return path == wiki or path.startswith(wiki + "/")


def subject(git, sha):
    return _decode(run_bytes(git, "log", "-1", "--format=%h %s", sha)).strip()[:100]


def branch_candidates(git, base, ref):
    """Counter of paths a branch changes against its merge base with `base`."""
    out = run_bytes(git, "merge-base", base, ref, ok=(0, 1))
    if out.strip():
        raw = run_bytes(git, "diff", "--relative", "--name-only", "-z", "%s...%s" % (base, ref), "--", ".")
    else:
        raw = run_bytes(git, "diff", "--relative", "--name-only", "-z", empty_tree(git), ref, "--", ".")
    return Counter(parse_names(raw))


def emit(chosen, prefix=""):
    print(maplib.MAP_MARKER)
    print(maplib.MAP_HEADING)
    print()
    for path in chosen:
        print("- `%s%s` — TODO: say what to read it for" % (prefix, path))


def pickable(git, commit, path, cfg):
    return (not in_wiki(path, cfg) and "`" not in path and "\n" not in path
            and maplib.blob_at(git, commit, git.project_path(path)) is not None)


def suggest(root, cfg, opts):
    git = wl.Git(wl.git_root_for(root))
    if not git.ok:
        print("map.py suggest needs a Git checkout", file=sys.stderr)
        return 2
    base = cfg["main_ref"]
    base_commit = git.commit(base)
    if opts.branch:
        branch = git.commit(opts.branch)
        if branch is None or base_commit is None:
            print("`%s` or `%s` does not resolve" % (opts.branch, base), file=sys.stderr)
            return 2
        counts = branch_candidates(git, base, opts.branch)
        paths = sorted(counts, key=lambda p: (-counts[p], p))
        chosen = [p for p in paths if pickable(git, branch, p, cfg)][:opts.max]
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
    prefix = ref + ":" if opts.ref and tip != base_commit else ""
    if opts.commit:
        shas = []
        for given in opts.commit:
            sha = git.commit(given)
            if sha is None:
                print("commit `%s` does not resolve" % given, file=sys.stderr)
                return 2
            if sha not in shas:
                shas.append(sha)
    else:
        shas = search_commits(git, tip, opts.terms)
    tree = empty_tree(git)
    boundaries = shallow_boundaries(git)
    touched, lines, used, skipped = Counter(), Counter(), [], 0
    for sha in shas:
        files = commit_files(git, sha, tree, boundaries)
        if files is None:
            skipped += 1
            continue
        used.append(sha)
        merged = {}
        for path, n in files:
            merged[path] = merged.get(path, 0) + n
        for path, n in merged.items():
            touched[path] += 1
            lines[path] += n
    if skipped:
        print("note: skipped %d commit(s) at a shallow-clone boundary (history unknown)" % skipped,
              file=sys.stderr)
    ranked = sorted(touched, key=lambda p: (-touched[p], -lines[p], p))
    chosen = [p for p in ranked if pickable(git, tip, p, cfg)][:opts.max]
    if not chosen:
        print("no candidate files found", file=sys.stderr)
        return 1
    emit(chosen, prefix)
    print()
    print("Commits used (%d%s): %s" % (len(used), ", first 10 shown" if len(used) > 10 else "",
                                       "; ".join(subject(git, sha) for sha in used[:10])))
    return 0


def main(argv):
    # Keep a literal `--` for argparse: it ends option parsing, so a TERM may start with `-`.
    if "--" in argv:
        cut = argv.index("--")
        head, tail = argv[:cut], argv[cut:]
    else:
        head, tail = argv, []
    try:
        explicit_root, args = wl.split_root_arg(head)
    except wl.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=("template", "suggest"))
    parser.add_argument("--ref")
    parser.add_argument("--branch")
    parser.add_argument("--commit", action="append", metavar="SHA")
    parser.add_argument("--max", type=int, default=8)
    parser.add_argument("terms", nargs="*", metavar="TERM")
    opts = parser.parse_args(args + tail)
    if opts.cmd == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if not 1 <= opts.max <= maplib.MAX_ENTRIES:
        parser.error("--max must be 1 to %d" % maplib.MAX_ENTRIES)
    if any(not t for t in opts.terms):
        parser.error("a TERM must not be empty")
    if sum([bool(opts.terms), bool(opts.commit), bool(opts.branch)]) != 1:
        parser.error("give TERMs, --commit SHA, or --branch REF (exactly one)")
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
    except (GitFailure, wl.GitUnavailable) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
