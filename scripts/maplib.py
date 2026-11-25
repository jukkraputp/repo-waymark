"""The optional "Where to look" map of a topic page: parser and audit checks.

Used by audit.py, lookup.py, prompt_hook.py and map.py. Standard library only, Python 3.9+.
A map is a list of pointers into the code, written by a person:

    <!-- wiki-map v1 -->
    ## Where to look

    - `lib/app.dart` `App.start` — entry point
    - Trap: `lib/old_client.dart` — the legacy client; use `lib/client.dart`
    - `feature/x:lib/new.dart` — unmerged change, on its branch only

Any topic page may carry one, with or without a status block. Nothing here edits a page.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikilib as wl  # noqa: E402

MAP_MARKER = "<!-- wiki-map v1 -->"
MAP_HEADING = "## Where to look"
MAX_ENTRIES = 12
MAX_SYMBOL_FILE_BYTES = 1000000
FILE_MODES = ("100644", "100755")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_BULLET_HEAD = re.compile(r"^- (?P<trap>Trap: )?`(?P<pointer>[^`]+)`(?: `(?P<symbol>[^`]+)`)?(?P<rest>.*)$")
_H2 = re.compile(r"^ {0,3}##(?: |\t|$)")
_HEADING = re.compile(r"^ {0,3}## Where to look[ \t]*$")
# Any list item up to three spaces in, in any marker style. Only `- ` at column 0 is a map bullet.
_LIST_ITEM = re.compile(r"^ {0,3}(?:[*+-]|\d{1,9}[.)])(?:[ \t]|$)")
# The pointer (closed, or an unclosed prefix to the end of the line) and the symbol of a list item.
_POINTER_SPAN = re.compile(r"^(?P<lead> {0,3}(?:[*+-]|\d{1,9}[.)])[ \t]+(?:Trap: )?)(?:`[^`]*`|`.*)(?: `[^`]*`)?")
_ROLE = re.compile(r"^ — (?P<role>\S.*)$")
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
_SYMBOL_SPLIT = re.compile(r"\.|::|#")


class MapEntry(object):
    """One map bullet. `ref` is None for a pointer into `main_ref`."""
    __slots__ = ("pointer", "ref", "path", "symbol", "role", "trap", "lineno", "line_suffix")

    def __init__(self, pointer, ref, path, symbol, role, trap, lineno, line_suffix):
        self.pointer, self.ref, self.path, self.symbol = pointer, ref, path, symbol
        self.role, self.trap, self.lineno, self.line_suffix = role, trap, lineno, line_suffix


class MapSection(object):
    """A parsed map. `issues` are S18 problem texts; `start`/`end` are the section's line indexes
    (heading included, end exclusive) or None when the heading is missing."""

    def __init__(self):
        self.entries = []
        self.issues = []
        self.marker_idx = None
        self.start = None
        self.end = None

    @property
    def usable(self):
        return not self.issues and bool(self.entries)


def _fenced_lines(lines):
    """Flags for lines inside (or opening or closing) a backtick or tilde code fence."""
    flags, fence = [], None
    for line in lines:
        m = _FENCE.match(line)
        if fence is None:
            if m and m.group(1)[0] == "`" and "`" in line[m.end():]:
                m = None   # CommonMark: a backtick fence's info string may not contain a backtick
            flags.append(bool(m))
            if m:
                fence = m.group(1)
        else:
            flags.append(True)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line.strip().strip(fence[0]):
                fence = None
    return flags


def _section_end(lines, fenced, start):
    """Index where the map section starting at line `start` stops: the next H2 or HTML comment line
    outside a fence, else the end of the text. The parser and `mask_pointers` both use this."""
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if not fenced[j] and (_H2.match(lines[j]) or (stripped.startswith("<!--") and stripped.endswith("-->"))):
            return j
    return len(lines)


def split_pointer(pointer):
    """(ref or None, path, line suffix or None) of a backticked pointer, or None when unusable."""
    suffix = None
    body = pointer
    m = _LINE_SUFFIX.search(pointer)
    if m:
        suffix = m.group(0)[1:]
        body = pointer[:m.start()]
    ref = None
    path = body
    if ":" in body:
        ref, _, path = body.partition(":")
        if not ref:
            return None
    if not path.strip():
        return None
    return ref, path, suffix


def parse_map(text):
    """Parse the map section of a page's text; None when the page has no map marker.

    Markers inside code fences are ignored. Parsing never rewrites text."""
    lines = text.replace("\r\n", "\n").split("\n")
    fenced = _fenced_lines(lines)
    markers = [i for i, line in enumerate(lines) if not fenced[i] and line.strip() == MAP_MARKER]
    if not markers:
        return None
    section = MapSection()
    section.marker_idx = markers[0]
    if len(markers) > 1:
        section.issues.append("a page may have only one map section; second `%s` on line %d"
                              % (MAP_MARKER, markers[1] + 1))
    status = next((i for i, line in enumerate(lines) if not fenced[i] and line.strip() == wl.MARKER), None)
    if status is not None:
        history = next((i for i in range(status + 1, len(lines))
                        if not fenced[i] and lines[i].strip() == wl.HISTORY_MARKER), None)
        if history is None or markers[0] < history:
            section.issues.append("on a status page the map must come after `%s`, because the status block "
                                  "ends there; move the section to the end of the page" % wl.HISTORY_MARKER)
    i = markers[0] + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _HEADING.match(lines[i]):
        section.issues.append("`%s` must be followed by the heading `%s`" % (MAP_MARKER, MAP_HEADING))
        return section
    section.start = i
    end = _section_end(lines, fenced, i)
    section.end = end
    count = 0
    for j in range(i + 1, end):
        line = lines[j].rstrip()
        if fenced[j]:
            continue
        if not line.startswith("- "):
            if _LIST_ITEM.match(line):
                section.issues.append("line %d: use `- ` at the start of the line for a map bullet (`*`, `+`, "
                                      "`-` plus a tab, numbered and indented items are not supported)" % (j + 1))
            continue
        count += 1
        head = _BULLET_HEAD.match(line)
        if not head:
            section.issues.append("line %d: a bullet must start with a backticked pointer" % (j + 1))
            continue
        role = _ROLE.match(head.group("rest"))
        if not role:
            section.issues.append("line %d: a bullet needs ` — ` and a role after the pointer" % (j + 1))
            continue
        split = split_pointer(head.group("pointer"))
        if split is None:
            section.issues.append("line %d: pointer `%s` has no path" % (j + 1, head.group("pointer")))
            continue
        ref, path, suffix = split
        section.entries.append(MapEntry(head.group("pointer"), ref, path, head.group("symbol"),
                                        role.group("role").strip(), bool(head.group("trap")), j + 1, suffix))
    if count == 0:
        section.issues.append("the map has no bullets (use 1 to %d)" % MAX_ENTRIES)
    elif count > MAX_ENTRIES:
        section.issues.append("the map has %d bullets (max %d)" % (count, MAX_ENTRIES))
    return section


def load_map(path):
    """The page's map section, or None when it has none or cannot be read. Never raises."""
    try:
        return parse_map(wl.read(path))
    except Exception:
        return None


def usable_map(path):
    """The page's map when it is valid and has entries, else None."""
    section = load_map(path)
    return section if section is not None and section.usable else None


def mask_pointers(text):
    """The text with the pointer and symbol of every map list item removed, valid map or not.

    Other scans (wikilinks, links, release tags) must not read pointer paths: `src/[[helper]].py`
    or a tag-like path is a path, not a link or a release. Role text stays visible. An unclosed
    pointer prefix is masked to the end of its line. Sections are bounded exactly as the parser
    bounds them, and line numbers are preserved."""
    lines = text.split("\n")
    fenced = _fenced_lines([l.rstrip("\r") for l in lines])
    markers = [i for i, l in enumerate(lines) if not fenced[i] and l.strip() == MAP_MARKER]
    if not markers:
        return text
    for m in markers:
        start = m
        k = m + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k < len(lines) and not fenced[k] and _HEADING.match(lines[k].rstrip("\r")):
            start = k
        for j in range(start + 1, _section_end(lines, fenced, start)):
            if not fenced[j]:
                lines[j] = _POINTER_SPAN.sub(lambda mo: mo.group("lead"), lines[j], count=1)
    return "\n".join(lines)


strip_pointers = mask_pointers


def _page_file(root, cfg, name):
    """Absolute path of a plain top-level topic file inside the wiki directory, else None."""
    if not isinstance(name, str) or not name.endswith(".md") or name != os.path.basename(name) \
            or name in (".md", "..md") or "\0" in name:
        return None
    wiki = os.path.realpath(wl.wiki_dir(root, cfg))
    path = os.path.join(wiki, name)
    real = os.path.realpath(path)
    if os.path.dirname(real) != wiki or not os.path.isfile(real):
        return None
    # Same eligibility as the wiki's topic inventory: no MEMORY.md, index.md or index-*.md.
    if name not in {os.path.basename(p) for p in wl.page_files(root, cfg)}:
        return None
    return path


def parsed_map_for_page(root, cfg, name):
    """The parsed map of a top-level topic page (valid or not), or None. Never reads outside the wiki."""
    path = _page_file(root, cfg, name)
    return load_map(path) if path else None


def map_for_page(root, cfg, name):
    """The valid, non-empty map of a top-level topic page, or None. Shared by lookup and the hook."""
    section = parsed_map_for_page(root, cfg, name)
    return section if section is not None and section.usable else None


class GitError(Exception):
    """A Git read failed (not "the file is absent"); only raised when blob_at is called strict."""


def blob_at(git, commit, tree_path, strict=False):
    """Object id of the regular file at `commit:tree_path`, else None.

    A directory, symlink or submodule is not a file. `tree_path` is relative to the Git top level
    (use `git.project_path`). With `strict`, a failing `git ls-tree` raises GitError instead of
    reading as "not a file"."""
    key = ("blob", commit, tree_path)
    if key not in git._cache:
        found = None
        rc, out = git.run("ls-tree", "--full-tree", "-z", commit, "--", tree_path)
        if rc != 0 and strict:
            raise GitError("git ls-tree failed (exit %d) for %s:%s" % (rc, commit[:12], tree_path))
        if rc != 0:
            return None     # a failure is not cached, so a later strict call still sees it
        for record in out.split("\0"):
            meta, _, name = record.partition("\t")
            if name == tree_path:
                parts = meta.split()
                if len(parts) == 3 and parts[1] == "blob" and parts[0] in FILE_MODES:
                    found = parts[2]
                break
        git._cache[key] = found
    return git._cache[key]


def blob_text(git, oid):
    """Text of a blob, or None when it is larger than MAX_SYMBOL_FILE_BYTES or unreadable."""
    key = ("text", oid)
    if key not in git._cache:
        text = None
        rc, out = git.run("cat-file", "-s", oid)
        if rc == 0 and out.strip().isdigit() and int(out.strip()) <= MAX_SYMBOL_FILE_BYTES:
            rc, body = git.run("cat-file", "blob", oid)
            if rc == 0:
                text = body
        git._cache[key] = text
    return git._cache[key]


def symbol_name(symbol):
    """The last segment of a symbol: `Type::member`, `Class#method`, `a.b()` give the member name."""
    head = symbol.partition("(")[0]
    parts = [p for p in _SYMBOL_SPLIT.split(head) if p.strip()]
    return parts[-1].strip() if parts else ""


def audit_section(section, git=None, main_ref=None):
    """[(code, message)] for one parsed map: S18 format errors, W7 missing files, W8 stale symbols.

    Without `git` only S18 and the line-suffix part of W8 run. W7 and W8 symbol checks resolve a
    plain pointer against `main_ref` and a `ref:path` pointer against its own ref."""
    out = [("S18", issue) for issue in section.issues]
    main_commit = None
    if git is not None:
        main_commit = git.commit(main_ref)
        if main_commit is None and any(e.ref is None for e in section.entries):
            out.append(("W7", "main_ref `%s` does not resolve; pointers without their own ref "
                              "were not verified" % main_ref))
    for entry in section.entries:
        if entry.line_suffix:
            out.append(("W8", "pointer `%s` carries a line number (:%s); line numbers go stale, "
                              "name a symbol instead" % (entry.pointer, entry.line_suffix)))
        if git is None:
            continue
        if entry.ref is not None:
            commit, where = git.commit(entry.ref), entry.ref
            if commit is None:
                out.append(("W7", "pointer `%s`: ref `%s` does not resolve" % (entry.pointer, entry.ref)))
                continue
        else:
            commit, where = main_commit, main_ref
            if commit is None:
                continue
        oid = blob_at(git, commit, git.project_path(entry.path))
        if oid is None:
            out.append(("W7", "pointer `%s` is not a file at `%s`" % (entry.pointer, where)))
            continue
        name = symbol_name(entry.symbol) if entry.symbol else ""
        if name:
            content = blob_text(git, oid)
            if content is not None and not re.search(r"(?<!\w)%s(?!\w)" % re.escape(name), content):
                out.append(("W8", "symbol `%s` (looked for `%s`) was not found in `%s` at `%s`"
                                  % (entry.symbol, name, entry.path, where)))
    return out
