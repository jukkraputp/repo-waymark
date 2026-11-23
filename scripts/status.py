#!/usr/bin/env python3
"""Status-block tooling: keep generated index lines and front-matter descriptions in step.

    python3 scripts/status.py [--root DIR] check
    python3 scripts/status.py [--root DIR] sync [page.md …]
    python3 scripts/status.py [--root DIR] template

A page opts in with `<!-- wiki-status v1 -->` under its H1. The block is the source of truth; the
page's single index line and its front-matter `description:` (set to the block's About) are derived.
`sync` rewrites exactly the affected lines and no other byte of the index file, never touches `As of`,
and is idempotent. `check` exits 1 on any difference and prints a unified diff.
"""
import difflib
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikilib as wl  # noqa: E402


def expected_for(root, page):
    """Return (problems, index_loc, index_line, desc_line) for an opted-in page. problems = [str]."""
    if page.status_error:
        return ["%s: %s" % (page.name, page.status_error)], None, None, None
    problems = []
    sync_command = " ".join((
        wl.script_command("status.py"), "--root", shlex.quote(root), "sync", shlex.quote(page.name),
    ))
    try:
        line = wl.render_index_line(page)
    except KeyError as exc:
        return ["cannot render: block lacks %s" % exc], None, None, None
    locs = wl.index_location(root, page.name, page.config)
    if len(locs) != 1:
        problems.append("%s: expected exactly one index line linking to it, found %d" % (page.name, len(locs)))
        loc = None
    else:
        loc = locs[0]
        if loc[2] != line:
            diff = "\n".join(difflib.unified_diff([loc[2]], [line], "index line (current)",
                                                  "index line (generated)", lineterm="", n=0))
            problems.append("%s: index line %s:%d differs from the status block. Run "
                            "`%s`.\n%s" % (page.name, os.path.basename(loc[0]), loc[1], sync_command, diff))
    desc = None
    if page.fm_desc_idx is not None:
        desc = "description: " + (page.get("About") or "")
        if page.lines[page.fm_desc_idx] != desc:
            problems.append("%s: front-matter description differs from About. Run "
                            "`%s`." % (page.name, sync_command))
    return problems, loc, line, desc


def check(root, pages=None):
    problems = []
    for pg in (pages if pages is not None else wl.status_pages(root)):
        problems += expected_for(root, pg)[0]
    return problems


def replace_line(path, index, replacement):
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as stream:
        lines = stream.read().split("\n")
    lines[index] = replacement + ("\r" if lines[index].endswith("\r") else "")
    wl.write_text(path, "\n".join(lines))


def sync(root, names, cfg=None):
    pages = wl.status_pages(root, cfg)
    if names:
        want = set(os.path.basename(n) for n in names)
        missing = want - set(p.name for p in pages)
        if missing:
            print("not opted-in pages (no marker): " + ", ".join(sorted(missing)), file=sys.stderr)
            return 1
        pages = [p for p in pages if p.name in want]
    rc = 0
    for pg in pages:
        problems, loc, line, desc = expected_for(root, pg)
        if loc is None or line is None:
            print("SKIP %s: %s" % (pg.name, "; ".join(p.split("\n")[0] for p in problems)), file=sys.stderr)
            rc = 1
            continue
        path, lineno, cur = loc
        changed = []
        if cur != line:
            replace_line(path, lineno - 1, line)
            changed.append("index line %s:%d" % (os.path.basename(path), lineno))
        if desc is not None and pg.lines[pg.fm_desc_idx] != desc:
            replace_line(pg.path, pg.fm_desc_idx, desc)
            changed.append("front-matter description")
        print("%s: %s" % (pg.name, ", ".join(changed) if changed else "already in sync"))
    return rc


def main(argv):
    try:
        explicit_root, args = wl.split_root_arg(argv[1:])
        if args in (["--help"], ["-h"]):
            print(__doc__)
            return 0
        root = wl.find_root(explicit_root)
        cfg = wl.config_for(root)
    except wl.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    cmd = args[0] if args else "check"
    if cmd == "template":
        sys.stdout.write(wl.status_template(cfg))
        return 0
    if cmd == "check":
        pages = wl.status_pages(root, cfg)
        probs = check(root, pages)
        for p in probs:
            print(p)
        print("STATUS CHECK pages=%d problems=%d" % (len(pages), len(probs)))
        return 1 if probs else 0
    if cmd == "sync":
        return sync(root, args[1:], cfg)
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
