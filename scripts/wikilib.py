"""Shared code for the wiki tools: tokenizer, index entries, scoring, status blocks, git helpers.

Used by prompt_hook.py, lookup.py, status.py and audit.py. Standard library only, Python 3.9+.
A page that carries `<!-- wiki-status v1 -->` under its H1 has a
`## Current status` block. The block is the single status record. The page's index line and its
front-matter `description` are GENERATED from it (`status.py sync`); nobody edits them by hand.
"""
import bisect
import glob
import json
import math
import os
import posixpath
import re
import shlex
import subprocess
import sys
import unicodedata
from collections import defaultdict
from functools import lru_cache

MARKER = "<!-- wiki-status v1 -->"
STATUS_HEADING = "## Current status"
HISTORY_MARKER = "<!-- history -->"

REQUIRED_KEYS = ["As of", "Summary", "About", "Decision", "Code", "Merged",
                 "Tested", "Unverified", "Published", "Next", "Superseded", "Keywords"]

VOCAB = {
    "Decision": ["proposed", "approved", "trial", "rejected", "superseded", "n/a", "unknown"],
    "Code": ["none", "uncommitted", "partial", "committed", "n/a", "unknown"],
    "Merged": ["no", "integration", "main", "partial", "n/a", "unknown"],
    "Tested": ["none", "partial", "passed", "failed", "unknown"],
}
PUBLISHED_PLAIN = ["no", "n/a", "unknown"]

TEMPLATE = """# <Neutral topic name: no status words (PLANNED/DRAFT/drafted/proposed/uncommitted) in the H1>

<!-- wiki-status v1 -->
## Current status

- **As of:** YYYY-MM-DD · checked at `<sha>` (`<branch you inspected>`)
- **Summary:** <=200 chars; the one sentence a reader needs; newest truth only
- **About:** <=160 chars; stable description of the topic, no status words
- **Decision:** <proposed|approved|trial|rejected|superseded|n/a|unknown> — <date, who, what; evidence pointer>
- **Code:** <none|uncommitted|partial|committed|n/a|unknown> — <commits `sha`, branch, pushed?; what is NOT committed>
- **Merged:** <no|integration|main|partial|n/a|unknown> — <in / not in `ref` for EVERY ref you name; never a bare main>
- **Tested:** <none|partial|passed|failed|unknown>
  - YYYY-MM-DD <what> @ <`sha` | `tag` | unknown>: <result> (<evidence>)
- **Unverified:** <list, or "none known">
- **Published:** <no|n/a|unknown|`<tag>`> — <date, tag → `sha`, merge `sha`, evidence>
- **Next:** <next blocker / owner action>
- **Superseded:** none
- **Keywords:** <8-25 comma-separated: plain-language synonyms a person would type, exact identifiers, version tags>
- **Canonical copy:** <optional: `<ref>:<path>` when the authoritative page lives on another branch>

<!-- history -->
## History

<old body here, oldest first; add a one-line banner to each superseded paragraph:>
> **Superseded YYYY-MM-DD:** <old claim> → <replacement> (see Current status).
"""

# ---------------------------------------------------------------- paths


CONFIG_NAME = ".repo-waymark.json"
DEFAULT_CONFIG = {
    "wiki_dir": ".claude/memory",
    "main_ref": "origin/main",
    "extra_fields": {},
    "release_tag_pattern": None,
    "merged_label": "main",
}
# The Merged value that means "in `main_ref`" is configurable; the other values are fixed.
MERGED_LABEL_DEFAULT = DEFAULT_CONFIG["merged_label"]
_MERGED_LABEL = re.compile(r"[a-z0-9][a-z0-9._/-]{0,31}")


class ConfigError(ValueError):
    """A repository or command-line configuration could not be used."""


@lru_cache(maxsize=32)
def _parse_config(path, text):
    try:
        values = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(values, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    for key in sorted(values.keys() - DEFAULT_CONFIG.keys()):
        print(f"{path}: warning: unknown configuration key {key!r}", file=sys.stderr)
    config = {key: values.get(key, default) for key, default in DEFAULT_CONFIG.items()}
    wiki_dir = config["wiki_dir"]
    if (not isinstance(wiki_dir, str) or not wiki_dir.strip() or os.path.isabs(wiki_dir)
            or ".." in wiki_dir.split(os.sep) or "\0" in wiki_dir):
        raise ConfigError(f"{path}: wiki_dir must be a relative directory within the repository")
    config["wiki_dir"] = os.path.normpath(wiki_dir)
    main_ref = config["main_ref"]
    if not isinstance(main_ref, str) or not main_ref.strip() or any(c.isspace() for c in main_ref):
        raise ConfigError(f"{path}: main_ref must be a Git reference")
    label = config["merged_label"]
    reserved = [token for token in VOCAB["Merged"] if token != MERGED_LABEL_DEFAULT]
    if (not isinstance(label, str) or not _MERGED_LABEL.fullmatch(label)
            or first_token(label) != label or label in reserved):
        raise ConfigError(f"{path}: merged_label must match [a-z0-9][a-z0-9._/-]{{0,31}}, must not end in '.', "
                          f"and must not be one of the other Merged values ({', '.join(reserved)})")
    pattern = config["release_tag_pattern"]
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ConfigError(f"{path}: release_tag_pattern must be a regular expression or null")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{path}: invalid release_tag_pattern: {exc}") from exc
    fields = config["extra_fields"]
    if not isinstance(fields, dict):
        raise ConfigError(f"{path}: extra_fields must be a JSON object")
    names = {_normalize_field_key(key) for key in REQUIRED_KEYS + ["Canonical copy"]}
    for name, field in fields.items():
        normalized = _normalize_field_key(name)
        if not normalized or normalized in names or name != name.strip() or re.search(r"[*:\r\n]", name):
            raise ConfigError(f"{path}: invalid or duplicate status field {name!r}")
        names.add(normalized)
        if not isinstance(field, dict):
            raise ConfigError(f"{path}: extra field {name!r} must be a JSON object")
        vocab = field.get("vocab")
        label = field.get("label")
        omit_when = field.get("omit_when")
        if (not isinstance(vocab, list) or not vocab
                or any(not isinstance(token, str) or not token
                       or any(c.isspace() for c in token) or first_token(token) != token
                       for token in vocab)):
            raise ConfigError(f"{path}: extra field {name!r} needs a non-empty vocab of single tokens")
        if not isinstance(label, str) or not label.strip() or re.search(r"[\r\n]", label):
            raise ConfigError(f"{path}: extra field {name!r} needs an index label")
        if omit_when is not None and omit_when not in vocab:
            raise ConfigError(f"{path}: extra field {name!r} omit_when must be in its vocab")
    return config


def config_for(root):
    path = os.path.join(os.path.abspath(root), CONFIG_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            return _parse_config(path, fh.read())
    except FileNotFoundError:
        return DEFAULT_CONFIG
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def split_root_arg(argv):
    """Extract the root option while preserving the rest of a command's arguments."""
    root, rest = None, []
    args = iter(argv)
    for arg in args:
        if arg == "--":
            rest.extend(args)
            break
        if arg == "--root":
            root = next(args, None)
            if not root or root.startswith("--"):
                raise ConfigError("--root requires a directory")
        elif arg.startswith("--root="):
            root = arg.partition("=")[2]
            if not root:
                raise ConfigError("--root requires a directory")
        else:
            rest.append(arg)
    return root, rest


def find_root(explicit_root=None, start=None):
    """Find a wiki from an explicit root, an environment override, or the current checkout.

    Without an override, the search walks up from `start`, else CLAUDE_PROJECT_DIR, else the
    process working directory, and stops at the first directory holding `.git`."""
    override = explicit_root if explicit_root is not None else os.environ.get("WAYMARK_ROOT")
    if override is not None:
        root = os.path.abspath(override)
        if os.path.isdir(wiki_dir(root, config_for(root))):
            return root
        raise ConfigError(f"No wiki found at {root}")

    start = root = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(root, CONFIG_NAME)):
            if os.path.isdir(wiki_dir(root, config_for(root))):
                return root
            raise ConfigError(f"No wiki found at {root}")
        if glob.glob(os.path.join(root, DEFAULT_CONFIG["wiki_dir"], "index-*.md")):
            return root
        parent = os.path.dirname(root)
        if parent == root or os.path.exists(os.path.join(root, ".git")):
            break
        root = parent
    raise ConfigError(f"No wiki found from {start}")


def find_hook_root(payload_cwd=None, explicit_root=None):
    """The wiki root for a hook: `--root`/WAYMARK_ROOT, else the hook payload's `cwd`, else
    CLAUDE_PROJECT_DIR or the process directory.

    The payload `cwd` follows Claude into a worktree or after `cd`; CLAUDE_PROJECT_DIR stays at
    the session's start root. When `cwd` holds no wiki, the search falls back to the project."""
    if (explicit_root is None and os.environ.get("WAYMARK_ROOT") is None
            and isinstance(payload_cwd, str) and payload_cwd and os.path.isdir(payload_cwd)):
        try:
            return find_root(start=payload_cwd)
        except ConfigError:
            pass
    return find_root(explicit_root)


def git_root_for(root):
    """Allow an exported wiki to check evidence against a separate Git checkout."""
    return os.environ.get("WAYMARK_GIT_ROOT") or root


def wiki_dir(root, cfg):
    return os.path.join(root, cfg["wiki_dir"])


def wiki_path(root, name="", cfg=None):
    cfg = config_for(root) if cfg is None else cfg
    return os.path.join(cfg["wiki_dir"], name)


def script_command(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    return "python3 " + shlex.quote(path)


def merged_vocab(config):
    """The Merged vocabulary, with the configured label standing for "in `main_ref`"."""
    label = config.get("merged_label", MERGED_LABEL_DEFAULT)
    return [label if token == MERGED_LABEL_DEFAULT else token for token in VOCAB["Merged"]]


def merged_label_issue(page):
    """A fix hint when a page writes the default `main` while another merged_label is configured."""
    label = page.config.get("merged_label", MERGED_LABEL_DEFAULT)
    if label == MERGED_LABEL_DEFAULT or first_token(page.get("Merged") or "") != MERGED_LABEL_DEFAULT:
        return None
    return (f"Merged: {MERGED_LABEL_DEFAULT} is ambiguous here; this repository sets merged_label to "
            f"{label!r} for code in `{page.config['main_ref']}`: write `Merged: {label}`")


def status_template(config):
    fields = "".join(
        f"- **{name}:** <{'|'.join(field['vocab'])}> — <evidence>\n"
        for name, field in config["extra_fields"].items()
    )
    template = TEMPLATE
    label = config.get("merged_label", MERGED_LABEL_DEFAULT)
    if label != MERGED_LABEL_DEFAULT:
        template = template.replace(
            "<%s>" % "|".join(VOCAB["Merged"]), "<%s>" % "|".join(merged_vocab(config)), 1
        ).replace("never a bare main>", "never a bare %s>" % label, 1)
    return template.replace("- **Tested:**", fields + "- **Tested:**", 1)


def read_raw(path):
    """(text with \\n line endings, original file used CRLF)."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        raw = fh.read()
    crlf = "\r\n" in raw
    return (raw.replace("\r\n", "\n") if crlf else raw), crlf


def read(path):
    return read_raw(path)[0]


def write_text(path, text, crlf=False):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(text.replace("\n", "\r\n") if crlf else text)


def page_files(root, cfg=None):
    """List top-level topic pages, excluding the wiki indexes and memory entry point."""
    cfg = config_for(root) if cfg is None else cfg
    out = []
    for p in sorted(glob.glob(os.path.join(wiki_dir(root, cfg), "*.md"))):
        b = os.path.basename(p)
        if b in ("MEMORY.md", "index.md") or b.startswith("index-"):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------- tokenizer

_FOLD = str.maketrans({"ð": "d", "Ð": "d", "þ": "th", "Þ": "th",
                       "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o",
                       "å": "a", "Å": "a", "ß": "ss", "œ": "oe", "Œ": "oe"})


def fold(text):
    """Lowercase and fold diacritics so accented and unaccented spellings can match."""
    t = text.lower().translate(_FOLD)
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


STOP = set("""a an and are as at be been before but by can could do does for from
get got has have how i if in into is it its just like make made may more most no
not of on once one only or other our out over should so some such than that the
their them then there these they this those to try use used using very was we
what when where which while who why will with would you your about after all any
because been being between both during each few further here him his me my need
now off own same she too under until up down was were will won new old also let
lets want wants using via per etc vs ok okay please thanks thank yes yeah sure
add adds added change changes changed fix fixes fixed update updates updated
work works working done make making run running check checking""".split())

_WORD = re.compile(r"[a-z][a-z0-9_.]*")


def words(folded):
    return [w.strip("._") for w in _WORD.findall(folded)]


def keywords(text):
    # bare filenames (index.md, audit.py) never count as words: they match unrelated lines by name alone
    return {w for w in words(fold(text)) if len(w) >= 4 and w not in STOP and not w.isdigit()
            and not FILE_EXT.search(w)}


_ID_TICK = re.compile(r"`([^`\s]{3,60})`")
_ID_CAMEL = re.compile(r"\b[A-Z]?[a-z]+(?:[A-Z][a-z0-9]+)+\b")
_ID_SNAKE = re.compile(r"\b[A-Za-z]\w*(?:[_.]\w+)+\b")
_ID_VERSION = re.compile(
    r"(?<!\w)(?<!\d\.)(?:(?:[A-Za-z]+-|v)\d+\.\d+(?:\.\d+)?|\d+\.\d+\.\d+)(?!\w|\.\d)"
)
_ISSUE_HEAD = r"(?<![\w-])(?<!\w\.)"
_ISSUE_TAIL = r"(?![\w-]|\.\w)"
_ID_ISSUE = re.compile(_ISSUE_HEAD + r"[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}" + _ISSUE_TAIL)
_ISSUE_DENY = {"UTF-8", "UTF-16", "UTF-32", "SHA-1", "SHA-256", "SHA-512",
               "ISO-8601", "ISO-639", "COVID-19", "MP-3"}
_ID_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


FILE_EXT = re.compile(
    r"\.(?:md|rst|txt|py|pyi|js|jsx|ts|tsx|mjs|cjs|java|kt|kts|scala|go|rs|c|cc|cpp|h|hpp|"
    r"cs|swift|rb|php|lua|luau|sh|bash|zsh|fish|ps1|pl|r|sql|json|toml|ya?ml|ini|xml|"
    r"html?|css|scss|less|vue|svelte|csv|svg|png|jpe?g|gif|webp|pdf)$",
    re.I,
)
DENY = set("""claude agents readme memory index repo-waymark repo_waymark waymark scripts
audit prompt_hook wikilib lookup status settings test_wiki test_waymark""".split())
MAX_IDENTS = 20


def _humps(t):
    return len(re.findall(r"[A-Z]", t)) if re.search(r"[a-z]", t) else 0


def is_issue_key(t):
    """Return whether an unfolded token qualifies as an issue-key identifier."""
    return t not in _ISSUE_DENY and bool(_ID_ISSUE.fullmatch(t))


def strong_shape(t):
    """Shapes distinctive enough to qualify alone and to be searched in page bodies."""
    if _ID_VERSION.fullmatch(t) or is_issue_key(t):
        return True
    if is_sha_shape(t):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", t):   # SCREAMING_SNAKE
        return True
    return _humps(t) >= 2


def is_sha_shape(t):
    return bool(re.fullmatch(r"[0-9a-f]{7,40}", t) and re.search(r"[a-f]", t) and re.search(r"[0-9]", t))


def _keep(tok):
    """Reject filenames, project-tool names and lowercase API names."""
    if "/" in tok:
        return True   # a path matches as a whole, never by its bare filename
    if FILE_EXT.search(tok):
        return False
    if fold(tok) in DENY or fold(tok.split(".")[0]) in DENY:
        return False
    if _ID_VERSION.fullmatch(tok):
        return True
    if "." in tok and not re.match(r"[A-Z]", tok.split(".")[0]):
        return False  # lowercase dotted names are too broad to act as identifiers
    return True


def identifiers(raw):
    """Exact identifiers from unfolded text: CamelCase, snake/SCREAMING/dotted, versions, issue keys and SHAs.

    Bare lowercase issue keys are out of scope. Hyphenated and dotted continuations are excluded.
    Filenames (index.md), tool names and lowercase API names are dropped. At most MAX_IDENTS are
    returned, strongest shape and longest first. De-duplicated case-insensitively.
    """
    found = []
    for rx in (_ID_TICK, _ID_VERSION, _ID_CAMEL, _ID_SNAKE, _ID_ISSUE):
        for m in rx.finditer(raw):
            tok = m.group(1) if rx is _ID_TICK else m.group(0)
            tok = tok.strip(".,;:!?)(]['\"")
            if rx is _ID_SNAKE:
                segs = re.split(r"[_.]", tok)
                if len(tok) < 5 or all(len(s) <= 2 for s in segs):
                    continue
            if rx is _ID_ISSUE and tok in _ISSUE_DENY:
                continue
            if len(tok) >= 3 and _keep(tok):
                found.append(tok)
    for m in _ID_SHA.finditer(raw):
        t = m.group(0)
        if is_sha_shape(t):
            found.append(t)
    seen, out = set(), []
    for t in found:
        k = fold(t)
        if k in seen or k in STOP or k.isdigit():
            continue
        seen.add(k)
        out.append(t)
    if len(out) > MAX_IDENTS:
        out.sort(key=lambda t: (0 if strong_shape(t) else 1, -len(t)))
        out = out[:MAX_IDENTS]
    return out


def ident_regex(ident):
    """Boundary-aware matcher for a FOLDED identifier inside a folded line."""
    f = fold(ident)
    if is_issue_key(ident):
        return re.compile(_ISSUE_HEAD + re.escape(f) + _ISSUE_TAIL)
    tail = r"(?![a-z0-9_]|\.[0-9])" if _ID_VERSION.fullmatch(ident) \
        else r"(?![a-z0-9_])"
    head = r"(?<![a-z0-9_])"
    if _ID_VERSION.fullmatch(ident):
        head += r"(?<!\d\.)(?<![a-z]-)"
    return re.compile(head + re.escape(f) + tail)


# ---------------------------------------------------------------- index entries

INDEX_LINE = re.compile(r"^- \[((?:[^\]\\]|\\.)+)\]\(([^)]+\.md)\)")
_STATUS_HEAD = r" — \*\*Status (\d{4}-\d{2}-\d{2}):\*\*"
_STATUS_PREFIX = re.compile(_STATUS_HEAD)
STATUS_LINE = re.compile(
    INDEX_LINE.pattern + _STATUS_HEAD + r" (.*?) — (.*?)(?: · About: .*)?$")


class Entry(object):
    __slots__ = ("cat", "file", "lineno", "line", "title", "page", "is_status", "folded", "words", "wordset")

    def __init__(self, cat, file, lineno, line):
        self.cat, self.file, self.lineno, self.line = cat, file, lineno, line
        m = INDEX_LINE.match(line)
        self.title = m.group(1).replace("\\]", "]").replace("\\[", "[") if m else line[2:60]
        self.page = m.group(2) if m else None
        self.is_status = bool(m and _STATUS_PREFIX.match(line, m.end()))
        self.folded = fold(line)          # identifiers match against the whole line
        match_text = line
        if self.is_status:
            sm = STATUS_LINE.match(line)
            if sm:                         # the generated status head must not pollute keyword matching
                match_text = line.replace(sm.group(4), "", 1)
        self.words = words(fold(match_text))
        self.wordset = frozenset(self.words)


def load_entries(root, cfg=None):
    cfg = config_for(root) if cfg is None else cfg
    entries = []
    for path in sorted(glob.glob(os.path.join(wiki_dir(root, cfg), "index-*.md"))):
        cat = os.path.basename(path)[len("index-"):-len(".md")].replace("-", " ")
        try:
            lines = read(path).split("\n")
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            if ln.startswith("- ["):
                entries.append(Entry(cat, path, i, ln))
    return entries


def indexed_status_pages(entries):
    """Page names whose generated index lines require a source block."""
    return {entry.page for entry in entries if entry.is_status}


class HitList(list):
    """Ranked hits; `.ident_df` maps each identifier to the number of index lines it matched."""
    ident_df = None


class Hit(object):
    def __init__(self, entry, kws, idents, score):
        self.entry, self.kws, self.idents, self.score = entry, kws, idents, score


IDENT_MAX_DF = 8  # an identifier found on more index lines than this behaves like a plain word
MIN_COMMON_DF = 3  # max_df_share never marks a keyword common on this many index lines or fewer


def _mask_identifiers(raw, idents):
    """Folded `raw` with every identifier span blanked out.

    Words inside an identifier (`proj` in PROJ-12) must not also count as keywords: a leftover
    word would otherwise match a longer, different identifier such as PROJ-123."""
    folded = fold(raw)
    for t in sorted(idents, key=len, reverse=True):
        folded = ident_regex(t).sub(lambda m: " " * len(m.group(0)), folded)
    return folded


def search(entries, raw, mode="hook", min_keywords=2, max_df_share=None):
    """Rank index entries for a prompt. mode 'hook': >=min_keywords words OR >=1 strong identifier.
    mode 'cli': any single match qualifies. With max_df_share, a keyword found on more than that
    share of index lines (never MIN_COMMON_DF lines or fewer) is common: a word-only hook match then
    also needs at least one keyword that is not common, so generic words cannot qualify on their
    own. Returns (hits, kws, idents); hits.ident_df has index df."""
    idents_raw = identifiers(raw)
    kws = set(keywords(_mask_identifiers(raw, idents_raw)))
    out = HitList()
    out.ident_df = {}
    if not kws and not idents_raw:
        return out, kws, []
    # keywords: expand each to the index vocabulary once (bisect over sorted words), drop dead ones
    vocab = sorted(set().union(*[e.wordset for e in entries])) if entries else []
    expanded = {}
    for k in kws:
        i = bisect.bisect_left(vocab, k)
        ws = set()
        while i < len(vocab) and vocab[i].startswith(k):
            ws.add(vocab[i])
            i += 1
        if ws:
            expanded[k] = ws
    kws = set(expanded)
    if len(kws) > 40:   # long paste: keep the longest (rarest) words
        kws = set(sorted(kws, key=lambda k: (-len(k), k))[:40])
        expanded = {k: expanded[k] for k in kws}
    idents = [(t, fold(t), ident_regex(t)) for t in idents_raw]
    n = max(len(entries), 1)
    df = defaultdict(int)
    idf_df = defaultdict(int)
    per = []
    for e in entries:
        mk = {k for k, ws in expanded.items() if not ws.isdisjoint(e.wordset)}
        mi = [t for t, f, rx in idents if f in e.folded and rx.search(e.folded)]
        for k in mk:
            df[k] += 1
        for t in mi:
            idf_df[t] += 1
        per.append((e, mk, mi))
    out.ident_df = {t: idf_df[t] for t, _, _ in idents}
    strong = {t for t, _, _ in idents if 0 < idf_df[t] <= IDENT_MAX_DF and strong_shape(t)}
    common = set()
    if max_df_share is not None:
        limit = max(MIN_COMMON_DF, max_df_share * len(entries))
        common = {k for k, count in df.items() if count > limit}
    for e, mk, mi in per:
        mi_strong = [t for t in mi if t in strong]
        mi_weak = [t for t in mi if t not in strong]
        eff = len(mk) + len(mi_weak)
        if mode == "hook":
            anchored = not common or bool(mi_weak) or bool(mk - common)
            ok = (eff >= min_keywords and anchored) or bool(mi_strong)
        else:
            ok = eff >= 1 or bool(mi_strong)
        if not ok:
            continue
        sc = sum(math.log(1 + n / (1 + df[k])) for k in mk)
        sc += sum(math.log(1 + n / (1 + idf_df[t])) for t in mi_strong) * 2
        sc += sum(math.log(1 + n / (1 + idf_df[t])) for t in mi_weak)
        out.append(Hit(e, sorted(mk), mi, sc))
    out.sort(key=lambda h: (-round(h.score, 6), -(len(h.kws) + len(h.idents)),
                            0 if h.entry.is_status else 1))
    return out, kws, [t for t, _, _ in idents]


def body_search(root, idents, cfg=None):
    """Exact (boundary-aware, case-insensitive) identifier search over non-archive page bodies.
    Returns {ident: [(page_basename, count, [first 3 line numbers])...]} ranked by count."""
    out = {}
    pages = []
    for p in page_files(root, cfg):
        try:
            text = read(p)
        except OSError:
            continue
        pages.append((os.path.basename(p), text.lower() if text.isascii() else fold(text)))
    for t in idents:
        rx = ident_regex(t)
        needle = fold(t)
        rows = []
        for name, low in pages:
            if needle not in low:   # cheap prefilter; the boundary regex only runs on candidate pages
                continue
            cnt, nums = 0, []
            for i, ln in enumerate(low.split("\n"), 1):
                if needle in ln and rx.search(ln):
                    cnt += 1
                    if len(nums) < 3:
                        nums.append(i)
            if cnt:
                rows.append((name, cnt, nums))
        rows.sort(key=lambda r: (-r[1], r[0]))
        out[t] = rows
    return out


def body_score(root, kws, limit=5, cfg=None):
    """--deep: rank pages by how many distinct keywords appear as word prefixes in the body."""
    rows = []
    rxs = {k: re.compile(r"(?<![a-z0-9_])" + re.escape(k)) for k in kws}
    for p in page_files(root, cfg):
        try:
            txt = fold(read(p))
        except OSError:
            continue
        m = [k for k, rx in rxs.items() if rx.search(txt)]
        if len(m) >= min(2, len(kws)) and m:
            rows.append((len(m), os.path.basename(p), sorted(m)))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows[:limit]


# ---------------------------------------------------------------- status block


def _normalize_field_key(key):
    return " ".join(key.casefold().split())


_FIELD_LABEL = r"\*\*([^*]+?):\*\*"
_TOP_LEVEL_FIELD = re.compile(r"^- " + _FIELD_LABEL + r" ?(.*)$")
_CHILD_BULLET = re.compile(r"^\s+- (.*)$")
_REQUIRED_CHILD_LABEL = re.compile(_FIELD_LABEL)
_LIST_MARKER = re.compile(r"(?:[-+*]|\d+[.)])(?:\s|$)")


class Page(object):
    """A parsed topic page. Optional text comes from an already-read Git blob."""

    def __init__(self, path, text=None, allow_unmarked_status=False, expect_status=False, config=None):
        self.path = path
        self.name = os.path.basename(path)
        self.config = DEFAULT_CONFIG if config is None else config
        extra_fields = self.config["extra_fields"]
        self.required_keys = REQUIRED_KEYS[:6] + list(extra_fields) + REQUIRED_KEYS[6:]
        self.vocab = {**VOCAB, "Merged": merged_vocab(self.config),
                      **{name: field["vocab"] for name, field in extra_fields.items()}}
        self._canonical_keys = {_normalize_field_key(key): key for key in self.required_keys}
        self.text, self.crlf = read_raw(path) if text is None else (text, False)
        self.lines = self.text.split("\n")
        self.h1_idx = next((i for i, l in enumerate(self.lines) if l.startswith("# ")), None)
        self.h1 = self.lines[self.h1_idx][2:].strip() if self.h1_idx is not None else ""
        self.marker_idx = next((i for i, l in enumerate(self.lines) if l.strip() == MARKER), None)
        self.fm = {}
        self.fm_desc_idx = None
        if self.lines and self.lines[0].strip() == "---":
            for i in range(1, len(self.lines)):
                if self.lines[i].strip() == "---":
                    break
                m = re.match(r"^(\w+):\s?(.*)$", self.lines[i])
                if m:
                    self.fm[m.group(1)] = m.group(2)
                    if m.group(1) == "description":
                        self.fm_desc_idx = i
        self.opted_in = self.marker_idx is not None
        self.fields = []   # [key, value, [children]]
        self.status_issues = []  # [(audit code, detail)]
        self.current_status_idx = None
        self.block_end = None
        self.history_idx = None
        if self.opted_in:
            i = self.marker_idx + 1
            while i < len(self.lines) and not self.lines[i].strip():
                i += 1
            if i < len(self.lines) and self.lines[i].rstrip() == STATUS_HEADING:
                self.current_status_idx = i
                self._parse_block(i + 1)
                self._check_structure()
            else:
                self.status_issues.append(("S17", "missing `## Current status` heading after the marker"))
        elif expect_status:
            self.status_issues.append(("S17", f"missing `{MARKER}` marker"))
        elif allow_unmarked_status:
            self.current_status_idx = next((i for i, line in enumerate(self.lines)
                                            if line.rstrip() == STATUS_HEADING), None)
            if self.current_status_idx is not None:
                self._parse_block(self.current_status_idx + 1)
        if (self.opted_in or expect_status) and any(code != "S3" for code, _ in self.status_issues) and all(
                code != "S17" for code, _ in self.status_issues):
            detail = ("generated status index entry has no valid source block" if expect_status
                      else "opted-in page has no valid status block")
            self.status_issues.append(("S17", detail))

    def _check_structure(self):
        if (self.h1_idx is None or self.marker_idx < self.h1_idx
                or any(line.strip() for line in self.lines[self.h1_idx + 1:self.marker_idx])):
            self.status_issues.append(("S1", "status block must sit directly under the H1 (only blank lines between)"))
        missing = [key for key in self.required_keys if self.get(key) is None]
        if missing:
            self.status_issues.append(("S1", "status block lacks required key(s): " + ", ".join(missing)))
        if self.history_idx is None:
            self.status_issues.append(("S1", "no `<!-- history -->` marker after the block"))
        label_issue = merged_label_issue(self)
        if label_issue:
            self.status_issues.append(("S3", label_issue))

    def _parse_block(self, start):
        n = len(self.lines)
        cur = None
        keys = {}
        self.block_end = next(
            (i for i in range(start, n)
             if self.lines[i].strip() == HISTORY_MARKER
             or (self.lines[i].startswith("## ") and self.lines[i].rstrip() != STATUS_HEADING)),
            n)
        for i in range(start, self.block_end):
            ln = self.lines[i]
            stripped = ln.strip()
            if not stripped:
                continue
            field = _TOP_LEVEL_FIELD.match(ln)
            if field and field.group(1).strip():
                label = field.group(1)
                normalized = _normalize_field_key(label)
                required = self._canonical_keys.get(normalized)
                key = required or label.strip()
                if normalized in keys:
                    self.status_issues.append(("S14", f"duplicate field {keys[normalized]} on line {i + 1}"))
                keys.setdefault(normalized, key)
                if required and label != required:
                    self.status_issues.append(("S15", f"non-canonical spelling of required field "
                                                      f"{label!r} on line {i + 1}; use {required!r}"))
                cur = [key, field.group(2).strip(), []]
                self.fields.append(cur)
                continue
            child = _CHILD_BULLET.match(ln)
            if child:
                value = child.group(1).strip()
                label = _REQUIRED_CHILD_LABEL.match(value)
                required = self._canonical_keys.get(_normalize_field_key(label.group(1))) if label else None
                if required:
                    self.status_issues.append(("S14", f"duplicate field {required} in child bullet on line {i + 1}"))
                    continue
                if cur is not None:
                    cur[2].append(value)
                    continue
            if cur is not None and ln.startswith("  ") and not _LIST_MARKER.match(stripped):
                cur[1] = (cur[1] + " " + stripped).strip()
                continue
            self.status_issues.append(("S16", f"unparseable line in status block at line {i + 1}"))
        for j in range(self.block_end, n):
            if self.lines[j].strip() == HISTORY_MARKER:
                self.history_idx = j
                break

    def get(self, key):
        for k, v, c in self.fields:
            if k == key:
                return v
        return None

    def children(self, key):
        for k, v, c in self.fields:
            if k == key:
                return c
        return []

    def keys(self):
        return [k for k, _, _ in self.fields]

    @property
    def status_error(self):
        if self.status_issues:
            return "INVALID status block: " + "; ".join(detail for _, detail in self.status_issues)
        return None

    def block_text(self):
        start = self.marker_idx if self.opted_in else self.current_status_idx
        return "\n".join(self.lines[start:self.block_end]) if start is not None else ""


def first_token(value):
    """First word of a field value, backticks and trailing punctuation removed."""
    if not value:
        return ""
    head = value.split(" — ")[0].strip()
    tok = head.split()[0] if head.split() else ""
    return tok.strip("`").rstrip(",.;:")


def as_of_date(page):
    m = re.search(r"\d{4}-\d{2}-\d{2}", page.get("As of") or "")
    return m.group(0) if m else None


def render_index_line(page):
    """The generated index line for an opted-in page. Raises KeyError if a required key is missing."""
    f = lambda k: page.get(k) or ""
    parts = ["decision " + first_token(f("Decision")), "code " + first_token(f("Code")),
             "merged " + first_token(f("Merged"))]
    for key, field in page.config["extra_fields"].items():
        token = first_token(f(key))
        if token and token != field.get("omit_when"):
            parts.append(field["label"] + " " + token)
    parts.append("tested " + first_token(f("Tested")))
    parts.append("published " + first_token(f("Published")))
    title = page.h1.replace("[", "\\[").replace("]", "\\]")
    date = as_of_date(page)
    if not date:
        raise KeyError("As of")
    return "- [%s](%s) — **Status %s:** %s — %s · About: %s · Keywords: %s" % (
        title, page.name, date, " · ".join(parts), f("Summary"), f("About"), f("Keywords"))


def index_location(root, page_name, cfg=None):
    """[(index_file, 1-based line number, line)] of index lines that link to the page."""
    cfg = config_for(root) if cfg is None else cfg
    out = []
    for path in sorted(glob.glob(os.path.join(wiki_dir(root, cfg), "index-*.md"))):
        for i, ln in enumerate(read(path).split("\n"), 1):
            m = INDEX_LINE.match(ln)
            if m and m.group(2) == page_name:
                out.append((path, i, ln))
    return out


def status_page(root, name, expect_status=False, cfg=None):
    """Read a topic with an opt-in marker or a generated status index entry."""
    cfg = config_for(root) if cfg is None else cfg
    if name and name == os.path.basename(name):
        try:
            page = Page(os.path.join(wiki_dir(root, cfg), name),
                        expect_status=expect_status, config=cfg)
        except OSError:
            pass
        else:
            return page if page.opted_in or page.status_issues else None
    if expect_status:
        page = Page(name, text="", config=cfg)
        page.status_issues.append(("S17", "generated status index entry has no readable topic page"))
        return page
    return None


def status_pages(root, cfg=None):
    cfg = config_for(root) if cfg is None else cfg
    indexed = indexed_status_pages(load_entries(root, cfg))
    names = {os.path.basename(path) for path in page_files(root, cfg)} | indexed
    pages = []
    for name in sorted(names):
        page = status_page(root, name, expect_status=name in indexed, cfg=cfg)
        if page is not None:
            pages.append(page)
    return pages


# ---------------------------------------------------------------- git


class GitUnavailable(Exception):
    pass


GIT_TIMEOUT = 5


class Git(object):
    """Read-only local Git access with bounded subprocesses and cached object lookups."""

    def __init__(self, root):
        self.root = root
        self._cache = {}
        self._tags = None
        self._prefix = None
        try:
            rc, _ = self.run("rev-parse", "--git-dir")
        except GitUnavailable:
            rc = 1
        self.ok = rc == 0

    def run(self, *args, input_text=None):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
        try:
            r = subprocess.run(
                ["git", "-C", self.root, *args], env=env, timeout=GIT_TIMEOUT,
                input=input_text, stdin=subprocess.DEVNULL if input_text is None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitUnavailable(f"git {args[0]} is unavailable: {exc}") from exc
        return r.returncode, r.stdout

    def project_path(self, path):
        """Translate a path relative to the selected project into a Git tree path."""
        if self._prefix is None:
            rc, out = self.run("rev-parse", "--show-prefix")
            if rc != 0:
                raise GitUnavailable("git could not resolve the project path")
            self._prefix = out.rstrip("\n")
        combined = posixpath.join(self._prefix, path)
        if not combined:
            return ""
        normalized = posixpath.normpath(combined)
        if combined.endswith("/") and not normalized.endswith("/"):
            return normalized + "/"
        return normalized

    def resolve(self, spec):
        """Full object id for a spec (`sha^{commit}`, `ref^{commit}`, `ref:path`) or None."""
        if "\n" in spec or "\r" in spec or "\0" in spec:
            return None
        if spec not in self._cache:
            rc, out = self.run("cat-file", "--batch-check=%(objectname)", input_text=spec + "\n")
            oid = out.rstrip("\n")
            self._cache[spec] = oid if rc == 0 and re.fullmatch(r"[0-9a-f]{40}", oid) else None
        return self._cache[spec]

    def commit(self, ref):
        return self.resolve(ref + "^{commit}")

    def is_ancestor(self, a, b):
        rc, _ = self.run("merge-base", "--is-ancestor", a, b)
        return rc == 0 if rc in (0, 1) else None

    def tags(self):
        if self._tags is None:
            rc, out = self.run("tag", "-l")
            self._tags = set(out.split()) if rc == 0 else set()
        return self._tags

    def tracked(self, relpath):
        rc, _ = self.run("ls-files", "--error-unmatch", "--", relpath)
        return rc == 0

    def show(self, spec):
        rc, out = self.run("show", spec)
        return out if rc == 0 else None

def backticked(text):
    return re.findall(r"`([^`]+)`", text or "")


SHA_TOKEN = re.compile(r"[0-9a-f]{7,40}")


def is_sha(tok):
    """Recognize hex revisions while filtering long numeric IDs."""
    if not SHA_TOKEN.fullmatch(tok):
        return False
    return not (tok.isdigit() and len(tok) > 12)


def block_shas(value):
    return [t for t in backticked(value) if is_sha(t)]


def block_refs(value):
    """Backticked non-SHA tokens that look like refs (no spaces, no path-with-extension)."""
    out = []
    for t in backticked(value):
        if is_sha(t) or " " in t or ":" in t:
            continue
        if re.fullmatch(r"[A-Za-z0-9._/-]+", t):
            out.append(t)
    return out
