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
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikilib as wl  # noqa: E402


# A plain YAML scalar must not start with an indicator, contain `: ` or ` #`, end with `:`, carry
# surrounding whitespace or control characters, or read as a non-string (null, bool, number).
_YAML_INDICATOR_START = re.compile(r"^(?:[,\[\]{}#&*!|>'\"%@`]|[-?:](?:\s|$))")
_YAML_NON_STRING = re.compile(
    r"^(?:~|null|Null|NULL|true|True|TRUE|false|False|FALSE|yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF"
    r"|[-+]?(?:0|[1-9][0-9_]*)|0o?[0-7_]+|0x[0-9a-fA-F_]+|0b[01_]+"
    r"|[-+]?(?:\.[0-9]+|[0-9][0-9_]*(?:\.[0-9_]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_YAML_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
                 "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
                 "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}
_YAML_HEX = {"x": 2, "u": 4, "U": 8}


def plain_scalar_ok(value):
    """Whether `value` can be written as a plain YAML scalar that reads back as the same string."""
    return bool(value) and value == value.strip() and not (
        _YAML_INDICATOR_START.match(value) or ": " in value or " #" in value or value.endswith(":")
        or any(ord(c) < 32 or ord(c) == 127 for c in value) or _YAML_NON_STRING.match(value))


def yaml_scalar(value):
    """`value` as a single-line YAML scalar: plain when safe, otherwise double-quoted."""
    if plain_scalar_ok(value):
        return value
    out = []
    for c in value:
        if c in ('"', "\\"):
            out.append("\\" + c)
        elif c == "\t":
            out.append("\\t")
        elif c == "\n":
            out.append("\\n")
        elif ord(c) < 32 or ord(c) == 127:
            out.append("\\x%02x" % ord(c))
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def read_yaml_scalar(text):
    """Decode a single-line YAML scalar (plain, single- or double-quoted); None when invalid or
    not one this tool would accept as a description."""
    text = text.strip()
    if text.startswith('"'):
        out, i = [], 1
        while i < len(text):
            c = text[i]
            if c == '"':
                return "".join(out) if i == len(text) - 1 else None
            if c == "\\":
                i += 1
                if i >= len(text):
                    return None
                e = text[i]
                if e in _YAML_HEX:
                    digits = text[i + 1:i + 1 + _YAML_HEX[e]]
                    if len(digits) != _YAML_HEX[e] or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                        return None
                    out.append(chr(int(digits, 16)))
                    i += _YAML_HEX[e]
                elif e in _YAML_ESCAPES:
                    out.append(_YAML_ESCAPES[e])
                else:
                    return None
            else:
                out.append(c)
            i += 1
        return None
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'"):
            return None
        body = text[1:-1]
        if "'" in body.replace("''", ""):
            return None
        return body.replace("''", "'")
    return text if plain_scalar_ok(text) else None


def description_span(page):
    """(first, end) line indexes of the front-matter description, continuation lines included."""
    first = page.fm_desc_idx
    end = first + 1
    i = end
    while i < len(page.lines):
        line = page.lines[i]
        if line.strip() == "---":
            break
        if line[:1] in (" ", "\t"):
            i += 1
            end = i
        elif not line.strip():
            i += 1
        else:
            break
    return first, end


def description_in_sync(page, about):
    """A single-line description whose YAML value equals About. A multi-line or invalid value
    (for example an unquoted `Cache: invalidation`) is drift that sync rewrites."""
    first, end = description_span(page)
    if end - first != 1:
        return False
    line = page.lines[first]
    if not line.startswith("description:"):
        return False
    value = line[len("description:"):]
    if value and value[0] not in " \t":
        return False
    if not value.strip():
        return about == ""   # an empty value written by earlier versions still means an empty About
    return read_yaml_scalar(value) == about


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
        about = page.get("About") or ""
        desc = "description: " + yaml_scalar(about)
        if not description_in_sync(page, about):
            problems.append("%s: front-matter description differs from About. Run "
                            "`%s`." % (page.name, sync_command))
    return problems, loc, line, desc


def check(root, pages=None):
    problems = []
    for pg in (pages if pages is not None else wl.status_pages(root)):
        problems += expected_for(root, pg)[0]
    return problems


def replace_line(path, index, replacement, end=None):
    """Replace lines[index:end] (one line by default) with a single replacement line."""
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as stream:
        lines = stream.read().split("\n")
    end = index + 1 if end is None else end
    lines[index:end] = [replacement + ("\r" if lines[index].endswith("\r") else "")]
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
        if desc is not None and not description_in_sync(pg, pg.get("About") or ""):
            first, end = description_span(pg)
            replace_line(pg.path, first, desc, end)
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
