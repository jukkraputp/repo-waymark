#!/usr/bin/env python3
"""Surface matching wiki entries for a Claude Code UserPromptSubmit hook.

Reads a JSON prompt from stdin or a test prompt from argv. Exact identifiers
can match on their own. Unmatched strong identifiers other than issue keys
can find topic pages by body search. Generic word matches need two distinct
keywords by default, at least one of them on no more than 3% of index lines.
Nothing is printed unless a match qualifies.
"""
import json
import os
import sys
from itertools import chain

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import wikilib as wl
except Exception:  # a broken helper must never break a prompt
    wl = None

MAX_BODY = 2
OUTPUT_CHARS = 8000
OPENING = "<wiki-index-matches>"
CLOSING = "</wiki-index-matches>"
HEADER = "Wiki pages matching this prompt. Open one before acting; check Git for anything newer."
# Status fields with these values carry no information in a preview.
EMPTY_STATUS_VALUES = ("unknown", "n/a", "none")
# Claude Code also fires UserPromptSubmit for turns it starts itself and passes harness text as the
# prompt. Skip a prompt that starts with one of these envelope tags: matching its boilerplate only
# injects unrelated entries.
HARNESS_ENVELOPE_TAGS = (
    "task-notification", "agent-message", "side-sessions-event", "ci-monitor-event",
    "teammate-message", "channel-message",
)


def _cap(text, n):
    if n <= 0:
        return ""
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _env_int(name, default):
    try:
        return max(0, int(os.environ.get("WAYMARK_HOOK_" + name, default)))
    except ValueError:
        return default


def _env_share(name, default):
    try:
        value = float(os.environ.get("WAYMARK_HOOK_" + name, default))
    except ValueError:
        return default
    return value if value >= 0 else default


def _escape_markup(text):
    """Neutralise every angle bracket in page- or index-derived text.

    Wiki text is untrusted: a page could otherwise close this wrapper or imitate the harness's
    own context tags (for example `</system-reminder>`). Only the hook's wrapper lines stay literal."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _block_status(page):
    """(date, head, summary) rendered from the topic's own parsed status block, or None.

    The cached index line can lag the page until `status.py sync` runs; the preview must show
    what the block says now, so render it with the same code that generates the index line."""
    if page is None or page.status_error or not page.opted_in:
        return None
    try:
        m = wl.STATUS_LINE.match(wl.render_index_line(page))
    except KeyError:
        return None
    return m.groups()[2:] if m else None


def _compact_status(date, head, summary):
    """'date · field value … — summary', dropping fields whose value says nothing."""
    parts = [p for p in head.split(" · ")
             if p.rpartition(" ")[2] not in EMPTY_STATUS_VALUES]
    return " · ".join([date] + parts) + " — " + summary


def _show(e, page, root, cfg, entry_chars, status_chars):
    path = wl.wiki_path(root, e.page or "", cfg)
    if page and page.status_error:
        text = "(%s) [%s](%s) — %s" % (e.cat, e.title, path, page.status_error)
    elif st := _block_status(page):
        text = "(%s) [%s](%s) — %s" % (e.cat, e.title, path, _compact_status(*st))
    elif e.is_status:
        text = "(%s) [%s](%s) — status unavailable" % (e.cat, e.title, path)
    else:
        im = wl.INDEX_LINE.match(e.line)
        entry = "[%s](%s)%s" % (e.title, path, e.line[im.end():]) if im else e.line[2:]
        return _cap(_escape_markup("(%s) %s" % (e.cat, entry.strip())), entry_chars)
    return _cap(_escape_markup(text), status_chars)


def _is_harness_envelope(text):
    stripped = text.lstrip()
    for tag in HARNESS_ENVELOPE_TAGS:
        if stripped.startswith("<" + tag):
            rest = stripped[len(tag) + 1:]
            if rest[:1] == ">" or rest[:1].isspace():
                return True
    return False


def _is_system_turn(payload):
    # promptSource and origin appear in transcripts but are not documented hook input;
    # honored only in case Claude Code starts passing them.
    origin = payload.get("origin")
    return payload.get("promptSource") == "system" or (
        isinstance(origin, dict) and origin.get("kind") not in (None, "user"))


def main():
    try:
        if wl is None:
            return 0
        explicit_root, args = wl.split_root_arg(sys.argv[1:])
        payload_cwd = None
        if args:
            raw = " ".join(args)
        else:
            data = sys.stdin.read()
            if data.strip().startswith("{"):
                payload = json.loads(data)
                if _is_system_turn(payload):
                    return 0
                raw = payload.get("prompt") or ""
                payload_cwd = payload.get("cwd")
            else:
                raw = data
        if not isinstance(raw, str) or not raw.strip() or _is_harness_envelope(raw):
            return 0
        # The payload cwd follows Claude into a worktree, so previews match the checkout the
        # Stop hook checks; CLAUDE_PROJECT_DIR stays at the session's start root.
        root = wl.find_hook_root(payload_cwd, explicit_root)
        cfg = wl.config_for(root)
        entries = wl.load_entries(root, cfg)
        indexed = wl.indexed_status_pages(entries)

        def status_page(name):
            return wl.status_page(root, name, expect_status=name in indexed, cfg=cfg)

        hits, _, idents = wl.search(
            entries, raw, mode="hook", min_keywords=_env_int("MIN_KEYWORDS", 2),
            max_df_share=_env_share("MAX_DF", 0.03),
        )
        shown = hits[:_env_int("MAX_HITS", 3)]
        entry_chars = _env_int("ENTRY_CHARS", 160)
        status_chars = _env_int("STATUS_CHARS", 220)
        body_lines = []
        # Issue-key shapes also match common labels such as HTTP-2 in page bodies.
        body_idents = [t for t in idents
                       if wl.strong_shape(t) and not wl.is_issue_key(t) and not hits.ident_df.get(t)]
        if body_idents and len(raw) <= 4096:
            seen = {h.entry.page for h in shown}
            title_of = {}
            for e in entries:
                if e.page and e.page not in title_of:
                    title_of[e.page] = e
            for ident, rows in wl.body_search(root, body_idents, cfg).items():
                if not rows or len(rows) > 10:   # too common to be a pointer
                    continue
                for page, cnt, _nums in rows:
                    if page in seen:
                        continue
                    e = title_of.get(page)
                    pg = status_page(page)
                    st = _block_status(pg)
                    if pg and pg.status_error:
                        tail = pg.status_error
                    elif st:
                        tail = _cap(" " + _compact_status(*st), 160)
                    elif e and not e.is_status:
                        tail = _cap(e.line[wl.INDEX_LINE.match(e.line).end():].strip(), 160)
                    else:
                        tail = ""
                    title = e.title if e else page
                    path = wl.wiki_path(root, page, cfg)
                    body_lines.append(_escape_markup(
                        "- (body: `%s` ×%d) [%s](%s) …%s" % (ident, cnt, title, path, tail)))
                    seen.add(page)
                    if len(body_lines) >= MAX_BODY:
                        break
                if len(body_lines) >= MAX_BODY:
                    break
        if not shown and not body_lines:
            return 0
        out = [OPENING, HEADER]
        budget = OUTPUT_CHARS - len(CLOSING) - 1
        prefix = _cap("\n".join(out), budget - 1) + "\n"
        previews = (
            "- " + _show(h.entry, status_page(h.entry.page), root, cfg, entry_chars, status_chars)
            for h in shown
        )
        for line in chain(previews, body_lines):
            remaining = budget - len(prefix) - 1
            if remaining <= 0:
                break
            prefix += _cap(line, remaining) + "\n"
        print(prefix + CLOSING)
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
