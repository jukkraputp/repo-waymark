#!/usr/bin/env python3
"""Audit wiki indexes, links, and status blocks without changing files.

Git checks use the local checkout. WAYMARK_GIT_ROOT can select another checkout.
Exit codes are 0 for success, 1 for findings, and 2 for usage or internal errors.
Warnings count as findings with --strict.
"""
import argparse
import collections
import datetime
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import status as status_mod  # noqa: E402
    import wikilib as wl  # noqa: E402
except ImportError as exc:
    if __name__ != "__main__":
        raise
    print(f"ERROR IMPORT: {exc}", file=sys.stderr)
    sys.exit(2)

# Apply the shared entry parser across each index file.
INDEX_LINE = re.compile(wl.INDEX_LINE.pattern, re.M)
# Slug-shaped wikilink targets only: kebab-case words, no spaces/commas/digits-only.
WIKILINK = re.compile(r"\[\[([a-z0-9]+(?:-[a-z0-9]+)+)(?:[|#][^\]]*)?\]\]")
MDLINK = re.compile(r"\]\(([^)\s]+)\)")
REPO_PATH = re.compile(r"(?:(?P<ref>[^\s:*<>]+):)?(?P<path>(?:[^/\s:*<>]+/)+[^/\s:*<>]*)"
                       r"(?::\d+(?:-\d+)?)?")
FORBIDDEN_H1 = re.compile(r"PLANNED|DRAFT|drafted|proposed|uncommitted", re.I)
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
EXTERNAL_LINK = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|//)", re.I)
EVIDENCE = re.compile(r"\bevidence\b|§|:\d+|https?://[^\s)>]+", re.I)
TESTED_AT = re.compile(r"\s@\s+(`[^`\s]+`|[^`\s:].*)")


class Findings(object):
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, code, page, msg):
        self.errors.append((code, page, msg))

    def warn(self, code, page, msg):
        self.warnings.append((code, page, msg))


def read_audit_text(path, findings):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except UnicodeDecodeError:
        findings.warn("UTF8", os.path.basename(path), "invalid UTF-8 bytes were replaced while reading")
        return wl.read(path)


def strip_loc(target):
    t = target.split("#")[0]
    return re.sub(r":\d+(?:-\d+)?$", "", t)


def missing_link_code(root, wiki_dir, pagedir, target):
    resolved = os.path.normpath(os.path.join(pagedir, target))
    if os.path.isabs(target) or os.path.commonpath([wiki_dir, resolved]) == wiki_dir:
        return None if os.path.exists(resolved) else "S10"
    repo_path = os.path.join(wl.git_root_for(root), os.path.relpath(resolved, root))
    return None if os.path.exists(repo_path) else "W6"


def check_status_paths(root, pg, git, findings):
    block = pg.block_text()
    pagedir = os.path.dirname(pg.path)
    wiki_dir = wl.wiki_dir(root, pg.config)
    for target in MDLINK.findall(block):
        path = strip_loc(target)
        if not path or EXTERNAL_LINK.match(path):
            continue
        code = missing_link_code(root, wiki_dir, pagedir, path)
        if code == "S10":
            findings.err(code, pg.name, "linked file does not exist: %s" % target)
        elif code == "W6":
            findings.warn(code, pg.name, "linked repo path does not exist: %s" % target)
    for slug in WIKILINK.findall(block):
        if not os.path.exists(os.path.join(wiki_dir, slug + ".md")):
            findings.err("S10", pg.name, "wikilink target does not exist: [[%s]]" % slug)

    # A slashed branch or tag has the same shape as an extensionless path.
    refs = set(wl.block_refs(pg.get("Merged") or ""))
    refs.update(wl.block_refs(pg.get("As of") or ""))
    refs.update(wl.block_refs(pg.get("Published") or ""))
    for line in pg.children("Tested"):
        revision = TESTED_AT.search(line)
        if revision and revision.group(1).startswith("`"):
            refs.add(revision.group(1)[1:-1])
    refs.add(pg.config["main_ref"])
    repo_root = wl.git_root_for(root)
    for tok in wl.backticked(block):
        if "@" in tok or "://" in tok:
            continue
        match = REPO_PATH.fullmatch(tok)
        if not match:
            continue
        ref, path = match.group("ref", "path")
        if ref:
            commit = git.commit(ref) if git else None
            if commit and not git.resolve("%s:%s" % (commit, git.project_path(path))):
                findings.err("S10", pg.name, "path does not exist in ref: %s" % tok)
        elif tok not in refs and not tok.startswith("refs/"):
            first_segment = path.partition("/")[0]
            plausible = os.path.exists(os.path.join(repo_root, first_segment)) or wl.FILE_EXT.search(path)
            if (plausible and not os.path.exists(os.path.join(repo_root, path))
                    and not (git and git.commit(tok))):
                findings.warn("W6", pg.name, "repo path does not exist: %s" % tok)


def check_status_page(root, pg, git, findings, today):
    n = pg.name
    if pg.status_issues:
        for code, detail in pg.status_issues:
            findings.err(code, n, "INVALID status block: " + detail)
        return
    block = pg.block_text()
    # S2 ----------------------------------------------------------------
    asof = wl.as_of_date(pg)
    asof_d = None
    try:
        asof_d = datetime.date.fromisoformat(asof) if asof else None
    except ValueError:
        asof_d = None
    if asof_d is None:
        findings.err("S2", n, "`As of` has no valid ISO date: %r" % pg.get("As of"))
    elif asof_d > today:
        findings.err("S2", n, "`As of` %s is in the future" % asof)
    checked_at = re.search(r"checked at `([0-9a-f]{7,40})`", pg.get("As of") or "")
    checked_at_sha = checked_at.group(1) if checked_at else None
    if checked_at_sha is None:
        findings.err("S2", n, "`As of` must say checked at `<sha>`")
    elif git and not git.commit(checked_at_sha):
        findings.err("S2", n, "`As of` checked-at SHA %s does not exist" % checked_at_sha)
    # S3 ----------------------------------------------------------------
    for key, vocab in pg.vocab.items():
        v = pg.get(key)
        if v is not None and wl.first_token(v) not in vocab:
            findings.err("S3", n, "%s must start with one of [%s], got %r" % (key, "|".join(vocab), wl.first_token(v)))
    pub = pg.get("Published")
    tag_match = re.match(r"`([^`\s]+)`(?=\W|$)", pub or "")
    if pub is not None and wl.first_token(pub) not in wl.PUBLISHED_PLAIN and not tag_match:
        findings.err("S3", n, "Published must be no|n/a|unknown or start with a backticked tag, got %r"
              % wl.first_token(pub))
    # S4 ----------------------------------------------------------------
    for p in status_mod.expected_for(root, pg)[0]:
        findings.err("S4", n, p)
    # S12 ---------------------------------------------------------------
    if len(pg.get("Summary") or "") > 200:
        findings.err("S12", n, "Summary is %d chars (max 200)" % len(pg.get("Summary")))
    if len(pg.get("About") or "") > 160:
        findings.err("S12", n, "About is %d chars (max 160)" % len(pg.get("About")))
    if FORBIDDEN_H1.search(pg.h1):
        findings.err("S12", n, "H1 contains a status word (PLANNED/DRAFT/drafted/proposed/uncommitted): %r" % pg.h1)
    # S11 ---------------------------------------------------------------
    sup = pg.get("Superseded")
    if sup is not None:
        entries = ([sup] if sup and sup.strip().lower() != "none" else []) + pg.children("Superseded")
        for e in entries:
            if "→" not in e and "replaced by" not in e:
                findings.err("S11", n, "Superseded entry lacks `→` or `replaced by`: %r" % e[:80])
    # S13 ---------------------------------------------------------------
    if pg.history_idx is not None and asof_d:
        for l in pg.lines[pg.history_idx:]:
            if re.match(r"^#{2,3} ", l):
                for d in ISO.findall(l):
                    try:
                        if datetime.date.fromisoformat(d) > asof_d:
                            findings.err("S13", n, "History heading dated %s is newer than As of %s: %r"
                                  % (d, asof, l[:70]))
                            break
                    except ValueError:
                        pass
    # S10 / W6 (no git needed for working-tree paths) --------------------
    check_status_paths(root, pg, git, findings)
    # Format checks that need no git (also run with --no-git) ------------
    merged = pg.get("Merged") or ""
    mtok = wl.first_token(merged)
    refs = wl.block_refs(merged)
    label = pg.config["merged_label"]
    if mtok in ("no", "integration", label, "partial") and not refs:
        findings.err("S6", n, "Merged: %s must name the target ref(s) in backticks" % mtok)
    if tag_match:
        if not ISO.search(pub):
            findings.err("S7", n, "Published %r needs a date" % tag_match.group(1))
        if not EVIDENCE.search(pub[tag_match.end():]):
            findings.err("S7", n, "Published %r needs an evidence pointer" % tag_match.group(1))
    ttok = wl.first_token(pg.get("Tested") or "")
    kids = pg.children("Tested")
    if ttok in ("partial", "passed", "failed") and not kids:
        findings.err("S8", n, "Tested: %s needs at least one dated child line" % ttok)
    tested_revisions = []
    for c in kids:
        if not ISO.match(c):
            findings.err("S8", n, "Tested line must start with a date: %r" % c[:70])
        if re.search(r"\b(void|stale)\b", c, re.I):
            findings.warn("W2", n, "Tested line %r marks its result void or stale for the current tip" % c[:60])
        mm = TESTED_AT.search(c)
        if not mm:
            findings.err("S8", n, "Tested line lacks an `@ <revision or label>`: %r" % c[:70])
        elif mm.group(1).startswith("`"):
            tested_revisions.append(mm.group(1).strip("`"))
    canon = pg.get("Canonical copy")
    canon_toks = []
    if canon and canon.strip().lower() not in ("none", "n/a"):
        canon_toks = [t for t in wl.backticked(canon) if ":" in t]
        if not canon_toks:
            findings.err("S9", n, "Canonical copy must name `<ref>:<path>` in backticks")
    if not git:
        return
    # S5 ----------------------------------------------------------------
    bare = set(t for t in re.findall(r"(?<![\w`/.-])[0-9a-f]{7,40}(?![\w`/-])", block) if wl.is_sha_shape(t))
    for sha in sorted((set(wl.block_shas(block)) | bare) - {checked_at_sha}):
        if not git.commit(sha):
            findings.err("S5", n, "SHA `%s` does not exist in git" % sha)
    # S6 / W3 -----------------------------------------------------------
    code_shas = [s for s in wl.block_shas(pg.get("Code") or "") if git.commit(s)]
    main_ref = pg.config["main_ref"]
    main_names = {main_ref, "refs/heads/" + main_ref, "refs/remotes/" + main_ref}
    for prefix in ("refs/heads/", "refs/remotes/"):
        if main_ref.startswith(prefix):
            main_names.add(main_ref[len(prefix):])
    resolved = {}
    for r in refs:
        resolved[r] = git.commit(r)
        if not resolved[r]:
            findings.err("S6", n, "Merged names ref `%s` which does not resolve" % r)
    live = [r for r in refs if resolved.get(r)]
    main_named = any(r in main_names for r in live)
    if mtok in (label, "integration") and not code_shas:
        findings.err("S6", n, "Merged: %s needs at least one backticked SHA in Code" % mtok)
    if mtok == label and code_shas:
        if not git.commit(main_ref):
            findings.err("S6", n, "Merged: %s but `%s` does not resolve" % (label, main_ref))
        else:
            for s in code_shas:
                if git.is_ancestor(s, main_ref) is False:
                    findings.err("S6", n, "Merged: %s but Code commit %s is not an ancestor of `%s`"
                                 % (label, s, main_ref))
    if mtok == "integration" and code_shas:
        cands = [r for r in live if r not in main_names]
        if not cands:
            findings.err("S6", n, "Merged: integration must name the integration ref alongside or instead of `%s`" % main_ref)
        for s in code_shas:
            if cands and not any(git.is_ancestor(s, r) for r in cands):
                findings.err("S6", n, "Merged: integration but Code commit %s is not in `%s`" % (s, "`, `".join(cands)))
            if main_named and git.is_ancestor(s, main_ref):
                findings.err("S6", n, "Merged: integration but Code commit %s is already in `%s`" % (s, main_ref))
    if mtok in ("no", "integration") and code_shas and live:
        tgt = [main_ref] if mtok == "integration" and main_named else []
        if mtok == "no":
            tgt = live
        if tgt and all(any(git.is_ancestor(s, r) for r in tgt) for s in code_shas):
            findings.warn("W3", n, "Merged says %s but every Code commit is an ancestor of %s" % (mtok, ", ".join(tgt)))
    # S7 ----------------------------------------------------------------
    if tag_match:
        tags = git.tags()
        tag = tag_match.group(1)
        if tag not in tags:
            findings.err("S7", n, "Published tag `%s` does not exist in git" % tag)
        else:
            mm = re.search(r"→\s*`([0-9a-f]{7,40})`", pub)
            if mm:
                peeled = git.commit("refs/tags/" + tag)
                if not peeled or not peeled.startswith(mm.group(1)):
                    findings.err("S7", n, "tag `%s` peels to %s, not %s" % (tag, (peeled or "?")[:9], mm.group(1)))
    # S8 / W2 -----------------------------------------------------------
    tested_shas = []
    for tok in tested_revisions:
        if wl.is_sha(tok):
            if not git.commit(tok):
                findings.err("S8", n, "Tested SHA `%s` does not exist" % tok)
            else:
                tested_shas.append(tok)
        elif tok not in git.tags():
            findings.err("S8", n, "Tested revision `%s` is neither a SHA nor an existing tag" % tok)
        else:
            tested_shas.append("refs/tags/" + tok)
    for t in tested_shas:
        if any(s != t and git.is_ancestor(t, s) and not git.is_ancestor(s, t) for s in code_shas):
            findings.warn("W2", n, "Tested revision %s predates a newer Code commit; that result may be void for the tip"
                   % t.replace("refs/tags/", ""))
    # S9 / W4 -----------------------------------------------------------
    if canon_toks:
        ref, _, path = canon_toks[0].partition(":")
        tip = git.commit(ref)
        if not tip:
            findings.err("S9", n, "Canonical ref `%s` does not resolve" % ref)
        elif not git.resolve("%s:%s" % (tip, git.project_path(path))):
            findings.err("S9", n, "Canonical copy %s:%s does not exist" % (ref, path))
        else:
            cited = re.search(r"`%s`\s*@\s*`([0-9a-f]{7,40})`" % re.escape(ref), pg.get("As of") or "")
            if cited and not tip.startswith(cited.group(1)):
                findings.warn("W4", n, "canonical ref `%s` moved: tip %s, block cites %s" % (ref, tip[:9], cited.group(1)))
            if git.is_ancestor(tip, "HEAD"):
                findings.warn("W4", n, "canonical ref `%s` is now an ancestor of HEAD (merged): replace this stub "
                       "with the merged page, keep the block" % ref)
    # W1 / W5 -----------------------------------------------------------
    if asof_d and (today - asof_d).days > 14:
        findings.warn("W1", n, "As of %s is %d days old and needs verification"
               % (asof, (today - asof_d).days))
    rel = wl.wiki_path(root, pg.name, pg.config)
    if not git.tracked(rel):
        findings.warn("W5", n, "page is not tracked by git")


def global_checks(root, config, pages, git, findings):
    release_tag_pattern = config["release_tag_pattern"]
    release_tags = re.compile(release_tag_pattern) if git and release_tag_pattern is not None else None
    cited_tags = collections.defaultdict(set)
    wiki_dir = wl.wiki_dir(root, config)
    for p in pages:
        txt = wl.read(os.path.join(wiki_dir, p))
        for target in MDLINK.findall(txt):
            path = strip_loc(target)
            if not path or EXTERNAL_LINK.match(path) or path.startswith("/") or any(c in path for c in "<{$*"):
                continue
            if missing_link_code(root, wiki_dir, wiki_dir, path):
                findings.warn("G1", p, "broken relative link: %s" % target)
        if release_tags:
            for match in release_tags.finditer(txt):
                if match.group():
                    cited_tags[match.group()].add(p)
    if cited_tags:
        for tag in sorted(cited_tags.keys() - git.tags()):
            ps = sorted(cited_tags[tag])
            findings.warn("G2", ps[0], "tag `%s` is cited (%d page%s) but no such git tag exists"
                          % (tag, len(ps), "" if len(ps) == 1 else "s"))


def check_pages(root, config, pages, git):
    findings = Findings()
    today = datetime.date.today()
    status_pages = wl.status_pages(root, config)
    for pg in status_pages:
        check_status_page(root, pg, git, findings, today)
    global_checks(root, config, pages, git, findings)
    return len(status_pages), findings


def report_git_unavailable(findings):
    print("SKIPPED git checks (git or repository not available)")
    findings.warn("GIT", "-", "git checks skipped: git or repository not available")


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", metavar="DIR", help="select the project root")
    parser.add_argument("--strict", action="store_true", help="treat warnings as findings")
    parser.add_argument("--no-git", action="store_true", help="skip Git checks")
    args = parser.parse_args(argv)
    try:
        root = wl.find_root(args.root)
        config = wl.config_for(root)
    except wl.ConfigError as exc:
        print(f"ERROR CONFIG: {exc}", file=sys.stderr)
        return 2
    wiki_dir = wl.wiki_dir(root, config)
    pages = {os.path.basename(p) for p in wl.page_files(root, config)}
    findings = Findings()

    targets: dict = collections.defaultdict(list)
    external: list = []
    print(f"PAGES {len(pages)}")
    for f in sorted(glob.glob(os.path.join(wiki_dir, "index-*.md"))):
        cat = os.path.basename(f)
        txt = read_audit_text(f, findings)
        lines = INDEX_LINE.findall(txt)
        print(f"  {cat}: {len(lines)} lines, ~{len(txt) / 4 / 1000:.1f}K tok")
        for _title, tgt in lines:
            if tgt.startswith("../") or "/" in tgt:
                external.append((cat, tgt))
            else:
                targets[tgt].append(cat)

    orphans = sorted(pages - set(targets))
    dangling = sorted(t for t in targets if t not in pages)
    dupes = {t: c for t, c in targets.items() if len(c) > 1}

    broken: collections.Counter = collections.Counter()
    where: dict = collections.defaultdict(set)
    for p in sorted(pages):
        txt = read_audit_text(os.path.join(wiki_dir, p), findings)
        for link in WIKILINK.findall(txt):
            if link + ".md" not in pages:
                broken[link] += 1
                where[link].add(p)

    def section(name, items, fmt):
        print(f"{name} {len(items)}")
        for it in items:
            print("  " + fmt(it))

    section("ORPHANS", orphans, lambda o: o)
    section("DANGLING", dangling, lambda d: f"{d}  <- {', '.join(targets[d])}")
    section("DUPES", sorted(dupes), lambda d: f"{d}  <- {', '.join(dupes[d])}")
    section("BROKEN WIKILINKS", broken.most_common(), lambda kv: f"[[{kv[0]}]] x{kv[1]}  in {', '.join(sorted(where[kv[0]]))}")
    section("EXTERNAL (not checked)", external, lambda e: f"{e[1]}  <- {e[0]}")

    # ---- status blocks ----------------------------------------------------
    git = None
    if args.no_git:
        print("SKIPPED git checks (--no-git)")
    else:
        g = wl.Git(wl.git_root_for(root))
        if g.ok:
            git = g
        else:
            report_git_unavailable(findings)
    try:
        status_count, page_findings = check_pages(root, config, sorted(pages), git)
    except wl.GitUnavailable:
        report_git_unavailable(findings)
        status_count, page_findings = check_pages(root, config, sorted(pages), None)

    findings.errors.extend(page_findings.errors)
    findings.warnings.extend(page_findings.warnings)
    print(f"STATUS PAGES {status_count} · ERRORS {len(findings.errors)} · WARNINGS {len(findings.warnings)}")
    for code, page, msg in findings.errors:
        print(f"  ERROR {code} {page}: {msg}")
    for code, page, msg in findings.warnings:
        print(f"  WARN  {code} {page}: {msg}")

    old_bad = bool(orphans or dangling or dupes or broken)
    if old_bad or findings.errors or (args.strict and findings.warnings):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
