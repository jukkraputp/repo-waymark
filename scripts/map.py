#!/usr/bin/env python3
"""Draft a "Where to look" map for a topic page. Prints text; never edits a page.

    python3 scripts/map.py template
    python3 scripts/map.py [--root DIR] suggest [--ref REF] [--base BASE] [--max N]

`template` prints an empty map section to paste at the end of a page (after `<!-- history -->` on a
status page). `suggest` reads Git and prints draft bullets for the files most often changed: the
last 200 commits of `main_ref` (or of --base), or with --ref the files that branch changes against
its merge base, written as `ref:path` pointers. Each bullet's role is a TODO for a person to write.
Exit 0 on success, 1 when nothing was found, 2 on usage, configuration or Git errors.
"""
import argparse
import os
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


def _paths(out):
    return [line for line in out.split("\n") if line.strip()]


def candidates(git, base, ref):
    """Counter of repository-relative paths changed, over at most MAX_COMMITS commits."""
    counts = Counter()
    if ref is None:
        rng = base
    else:
        rc, _ = git.run("merge-base", base, ref)
        if rc != 0:
            tree = empty_tree(git)
            if tree is None:
                return counts
            rc, out = git.run("diff", "--relative", "--name-only", "--diff-filter=AMR", tree, ref, "--", ".")
            counts.update(_paths(out) if rc == 0 else [])
            return counts
        rng = "%s..%s" % (base, ref)
    rc, out = git.run("log", "-n", str(MAX_COMMITS), "--no-merges", "--relative", "--name-only",
                      "--diff-filter=AMR", "--format=", rng, "--", ".")
    counts.update(_paths(out) if rc == 0 else [])
    return counts


def suggest(root, cfg, opts):
    git = wl.Git(wl.git_root_for(root))
    if not git.ok:
        print("map.py suggest needs a Git checkout", file=sys.stderr)
        return 2
    base = opts.base or cfg["main_ref"]
    if git.commit(base) is None:
        print("`%s` does not resolve; pass --base" % base, file=sys.stderr)
        return 2
    tip_ref = opts.ref
    if tip_ref is not None and git.commit(tip_ref) is None:
        print("`%s` does not resolve" % tip_ref, file=sys.stderr)
        return 2
    commit = git.commit(tip_ref or base)
    counts = candidates(git, base, tip_ref)
    chosen = []
    for path, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if "\n" in path or "`" in path or maplib.blob_at(git, commit, git.project_path(path)) is None:
            continue
        chosen.append(path)
        if len(chosen) >= opts.max:
            break
    if not chosen:
        print("no candidate files found", file=sys.stderr)
        return 1
    print(maplib.MAP_MARKER)
    print(maplib.MAP_HEADING)
    print()
    for path in chosen:
        print("- `%s%s` — TODO: say what to read it for" % (tip_ref + ":" if tip_ref else "", path))
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
    s.add_argument("--base")
    s.add_argument("--max", type=int, default=8)
    opts = parser.parse_args(args)
    if opts.cmd == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if not 1 <= opts.max <= maplib.MAX_ENTRIES:
        parser.error("--max must be 1 to %d" % maplib.MAX_ENTRIES)
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
