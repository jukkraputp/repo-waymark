#!/usr/bin/env python3
"""Ask the wiki a question from the command line.

    python3 scripts/lookup.py "Are we ready to launch?"
    python3 scripts/lookup.py --root /path/to/repo --json --max 3 "ReleaseManifest"

Same scoring as the UserPromptSubmit hook (wikilib.search), but recall-first: a single matching
keyword qualifies. For hits with a generated index entry and a valid opted-in status block, the
status is displayed from the source page. The page's `## History` may include superseded material.
Identifier matches in page bodies (exact,
case-insensitive) are listed with file:line.

Flags: --max N (default 5) · --json · --blocks all|top (default top = blocks for the top 3 status
hits) · --branches (show a `Canonical copy` at its resolved commit via read-only `git show`)
· --deep (when <3 index hits, also rank page bodies by keyword).
Exit 0 when matches are found, 1 when there are no matches, and 2 on usage or configuration errors.
"""
import argparse
import json
import os
import re
import shlex
import sys
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikilib as wl  # noqa: E402


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Search a project's wiki.")
    parser.add_argument("--root", metavar="DIR")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--blocks", choices=("all", "top"), default="top")
    parser.add_argument("--branches", action="store_true")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("query", nargs="*")
    opts = parser.parse_intermixed_args(argv)
    if opts.max < 0:
        parser.error("--max must be at least zero")
    return vars(opts), " ".join(opts.query)


def print_block(pg, indent="   "):
    print("%sCurrent status (as of %s):" % (indent, pg.get("As of") or "unknown"))
    for key, val, kids in pg.fields:
        if key in ("As of", "Keywords"):
            continue
        print("%s  %s: %s" % (indent, key, val))
        for k in kids:
            print("%s    - %s" % (indent, k))


def branch_view(root, pg):
    canon = pg.get("Canonical copy") or ""
    toks = [t for t in wl.backticked(canon) if ":" in t]
    if not toks:
        return None
    ref, _, path = toks[0].partition(":")
    git = wl.Git(wl.git_root_for(root))
    result = {"ref": ref, "path": path}
    try:
        if not git.ok:
            raise wl.GitUnavailable
        tip = git.commit(ref)
        if not tip:
            result["message"] = "branch tip could not be resolved"
            return result
        spec = "%s:%s" % (tip, git.project_path(path))
        result.update(commit=tip, command="git show " + shlex.quote(spec))
        txt = git.show(spec)
    except wl.GitUnavailable:
        result["message"] = "git unavailable while reading branch evidence"
        return result
    if txt is None:
        result["message"] = "branch page could not be read at " + spec
        return result
    branch = wl.Page(path, text=txt, allow_unmarked_status=True, config=pg.config)
    if branch.status_error:
        result["message"] = branch.status_error
    elif branch.current_status_idx is not None:
        result["status_block"] = branch.block_text().strip()
    else:
        result["message"] = "branch page has no Current status block; read the record at " + spec
        for line in reversed(branch.lines):
            if re.match(r"^#{2,6} +.*\b\d{4}-\d{2}-\d{2}\b", line):
                result["context_heading"] = line
                break
    return result


def print_branch(branch):
    if not branch:
        return
    commit = branch.get("commit")
    print("   Branch copy %s%s" % (branch["ref"], " at " + commit if commit else ""))
    if commit:
        print("     " + branch["command"])
    if "message" in branch:
        print("     " + branch["message"])
    if "context_heading" in branch:
        print("     Last dated heading (context only): " + branch["context_heading"])
    if "status_block" in branch:
        for line in branch["status_block"].splitlines():
            print("     " + line if line else "")


def main(argv):
    opts, query = parse_args(argv)
    try:
        root = wl.find_root(opts["root"])
        cfg = wl.config_for(root)
    except wl.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not query.strip():
        print(__doc__)
        return 1
    entries = wl.load_entries(root, cfg)
    indexed = wl.indexed_status_pages(entries)
    hits, kws, idents = wl.search(entries, query, mode="cli")
    top = hits[:opts["max"]]
    bodies = wl.body_search(root, [t for t in idents if wl.strong_shape(t)], cfg) if idents else {}
    deep = wl.body_score(root, kws, cfg=cfg) if (opts["deep"] and len(hits) < 3 and kws) else []

    @lru_cache(maxsize=None)
    def status_page(name):
        return wl.status_page(root, name, expect_status=name in indexed, cfg=cfg)

    if opts["json"]:
        res = {"query": {"keywords": sorted(kws), "identifiers": idents}, "hits": [], "body": {}, "deep": deep}
        for h in top:
            e = h.entry
            d = {"title": e.title, "page": wl.wiki_path(root, e.page or "", cfg), "category": e.cat,
                 "matched": h.kws, "idents": h.idents, "score": round(h.score, 3)}
            pg = status_page(e.page)
            if pg and pg.status_error:
                d["status_error"] = pg.status_error
            else:
                d["index_line"] = e.line[:400]
                if pg:
                    d["status"] = {k: ({"value": v, "children": c} if c else v) for k, v, c in pg.fields}
                    if opts["branches"]:
                        branch = branch_view(root, pg)
                        if branch:
                            d["branch"] = branch
            res["hits"].append(d)
        for t, rows in bodies.items():
            res["body"][t] = []
            for p, c, n in rows[:10]:
                item = {"page": p, "count": c, "lines": n}
                pg = status_page(p)
                if pg and pg.status_error:
                    item["status_error"] = pg.status_error
                res["body"][t].append(item)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if (top or any(bodies.values()) or deep) else 1

    print("query: keywords=[%s] identifiers=[%s]" % (", ".join(sorted(kws)), ", ".join(idents)))
    printed = bool(top)
    shown_blocks = 0
    for n, h in enumerate(top, 1):
        e = h.entry
        match = ", ".join(h.kws + h.idents)
        page_path = wl.wiki_path(root, e.page or "", cfg)
        print("%d. %s — %s  (%s; matched: %s)" % (n, e.title, page_path, e.cat, match))
        pg = status_page(e.page)
        if pg and pg.status_error:
            print("   " + pg.status_error)
        elif pg and e.is_status and (opts["blocks"] == "all" or shown_blocks < 3):
            print_block(pg)
            print("   Record: %s §Current status. Older material is under ## History." % page_path)
            shown_blocks += 1
            if opts["branches"]:
                print_branch(branch_view(root, pg))
        else:
            txt = e.line[wl.INDEX_LINE.match(e.line).end():].strip() if e.page else e.line
            print("   index line: " + (txt[:300] + ("…" if len(txt) > 300 else "")))
    if any(bodies.values()):
        printed = True
        print("Identifier matches in page bodies:")
        for t, rows in bodies.items():
            for p, c, nums in rows[:8]:
                pg = status_page(p)
                error = " — " + pg.status_error if pg and pg.status_error else ""
                print("  %s ×%d  %s:%s%s" % (t, c, p, ",".join(str(x) for x in nums), error))
            if len(rows) > 8:
                print("  … %d more pages" % (len(rows) - 8))
    if deep:
        printed = True
        print("Body keyword matches (--deep):")
        for cnt, p, m in deep:
            print("  %s  (%d keywords: %s)" % (p, cnt, ", ".join(m)))
    if not printed:
        return 1
    print("Note: keyword hits; open the page before acting. Status blocks are authoritative as of "
          "their date; check `git` for anything newer.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
