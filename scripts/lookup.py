#!/usr/bin/env python3
"""Ask the wiki a question from the command line.

    python3 scripts/lookup.py "Are we ready to launch?"
    python3 scripts/lookup.py --root /path/to/repo --json --max 3 "ReleaseManifest"

Same scoring as the UserPromptSubmit hook (wikilib.search), but recall-first: a single matching
keyword qualifies. For hits with a generated index entry and a valid opted-in status block, the
status is displayed from the source page. The page's `## History` may include superseded material.
Identifier matches in page bodies (exact,
case-insensitive) are listed with file:line.
Map bullet keywords can fill unused result slots after the index hits.

Flags: --max N (default 5) · --json · --blocks all|top (default top = blocks and Where to look maps for
the top 3 hits that have them; all = every hit) · --branches (show a `Canonical copy` at its resolved commit via read-only `git show`)
· --deep (also show up to --max map matches, and rank page bodies by keyword when <3 index hits).
Exit 0 when matches are found, 1 when there are no matches, and 2 on usage or configuration errors.
"""
import argparse
import json
import math
import os
import re
import shlex
import sys
from bisect import bisect_left
from collections import Counter
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402
import wikilib as wl  # noqa: E402

MAX_MAP_SEARCH_BYTES = 2_000_000
_ACRONYM_END = re.compile(r"([A-Z])([A-Z][a-z])")
_CASE_CHANGE = re.compile(r"([a-z0-9])([A-Z])")


def map_keywords(section):
    words = set()
    for entry in section.entries:
        if entry.trap:
            continue
        words.update(wl.keywords(entry.role))
        symbol = (entry.symbol or "").replace(".", " ").replace("_", " ")
        symbol = _CASE_CHANGE.sub(r"\1 \2", _ACRONYM_END.sub(r"\1 \2", symbol))
        words.update(wl.keywords(symbol))
        basename = os.path.splitext(os.path.basename(entry.path))[0]
        words.update(wl.keywords(basename.replace("_", " ")))
    return sorted(words)


def has_prefix(vocab, prefix):
    i = bisect_left(vocab, prefix)
    return i < len(vocab) and vocab[i].startswith(prefix)


def map_matches(root, cfg, prefixes, excluded, limit):
    """Rank usable maps by inverse frequency of matched keywords in the bounded scan."""
    if limit <= 0 or not prefixes:
        return []
    remaining = MAX_MAP_SEARCH_BYTES
    wiki = os.path.realpath(wl.wiki_dir(root, cfg))
    marker = maplib.MAP_MARKER.encode("ascii")
    candidates = []
    frequencies = Counter()
    for path in wl.page_files(root, cfg):
        if remaining <= 0:
            break
        name = os.path.basename(path)
        if name in excluded:
            continue
        real = os.path.realpath(path)
        if os.path.dirname(real) != wiki or not os.path.isfile(real):
            continue
        try:
            # Buffered reads can prefetch beyond the remaining physical-byte budget.
            with open(real, "rb", buffering=0) as fh:
                data = fh.read(remaining)
                remaining -= len(data)
                if remaining == 0 and os.fstat(fh.fileno()).st_size > len(data):
                    break  # Never rank a partial page as a complete map.
        except OSError:
            continue
        if marker not in data:
            continue
        page = data.decode("utf-8", errors="replace")
        section = maplib.parse_map(page)
        if not section or not section.usable:
            continue
        vocab = map_keywords(section)
        matched = sorted(k for k, forms in prefixes.items() if any(has_prefix(vocab, f) for f in forms))
        if matched:
            heading = re.search(r"^# (.+)$", page, re.MULTILINE)
            title = heading[1].strip() if heading else os.path.splitext(name)[0]
            candidates.append((name, title, matched, section))
            frequencies.update(matched)
    matches = [(name, title, matched, section, math.fsum(1 / frequencies[k] for k in matched))
               for name, title, matched, section in candidates]
    matches.sort(key=lambda match: (-match[4], match[0]))
    return matches[:limit]


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


def map_lines(entry):
    return "%s`%s`%s — %s" % ("Trap: " if entry.trap else "", entry.pointer,
                              " `%s`" % entry.symbol if entry.symbol else "", entry.role)


def print_map(section, indent="   "):
    print("%sWhere to look:" % indent)
    for entry in section.entries:
        print("%s  - %s" % (indent, map_lines(entry)))


def map_json(section):
    return [{"pointer": m.pointer, "path": m.path, "ref": m.ref, "symbol": m.symbol,
             "role": m.role, "trap": m.trap} for m in section.entries]


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
    map_limit = opts["max"] if opts["deep"] else opts["max"] - len(top)
    map_hits = map_matches(root, cfg, hits.query_prefixes, {h.entry.page for h in top}, map_limit)
    bodies = wl.body_search(root, [t for t in idents if wl.strong_shape(t)], cfg) if idents else {}
    deep = wl.body_score(root, kws, cfg=cfg) if (opts["deep"] and len(hits) < 3 and kws) else []

    @lru_cache(maxsize=None)
    def status_page(name):
        return wl.status_page(root, name, expect_status=name in indexed, cfg=cfg)

    def page_map(name):
        return maplib.map_for_page(root, cfg, name)

    map_slots = 3 if opts["blocks"] == "top" else len(top) + len(map_hits)
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
            section = page_map(e.page) if e.page else None
            if section:
                d["map"] = map_json(section)
            res["hits"].append(d)
        for name, title, matched, section, score in map_hits:
            res["hits"].append({"title": title, "page": wl.wiki_path(root, name, cfg), "category": "map match",
                                "matched": matched, "idents": [], "score": round(score, 3), "map": map_json(section)})
        for t, rows in bodies.items():
            res["body"][t] = []
            for p, c, n in rows[:10]:
                item = {"page": p, "count": c, "lines": n}
                pg = status_page(p)
                if pg and pg.status_error:
                    item["status_error"] = pg.status_error
                res["body"][t].append(item)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if (top or map_hits or any(bodies.values()) or deep) else 1

    print("query: keywords=[%s] identifiers=[%s]" % (", ".join(sorted(kws)), ", ".join(idents)))
    printed = bool(top or map_hits)
    shown_blocks = 0
    for n, h in enumerate(top, 1):
        e = h.entry
        match = ", ".join(h.kws + h.idents)
        page_path = wl.wiki_path(root, e.page or "", cfg)
        print("%d. %s — %s  (%s; matched: %s)" % (n, e.title, page_path, e.cat, match))
        section = page_map(e.page) if e.page and map_slots > 0 else None
        if section:
            print_map(section)
            map_slots -= 1 if opts["blocks"] == "top" else 0
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
    for n, (name, title, matched, section, _) in enumerate(map_hits, len(top) + 1):
        print("%d. %s — %s  (map match; matched: %s)" % (n, title, wl.wiki_path(root, name, cfg), ", ".join(matched)))
        if map_slots > 0:
            print_map(section)
            map_slots -= 1
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
