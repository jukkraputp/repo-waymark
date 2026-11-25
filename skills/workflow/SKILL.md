---
name: workflow
description: Use when starting work in a repository with a Repo Waymark wiki, checking a project's current status or previous decisions, or maintaining a topic page after its status changes.
---

# Repository wiki workflow

Use the repository's wiki for project memory. Identify the project root and wiki directory before reading or editing. The default is `.claude/memory/`; see [configuration](reference/config.md) for overrides. If the repository has no wiki, continue with its existing project guidance.

## Find the source page

1. At the start of work, read `MEMORY.md` in the wiki, if present, for the category map. Use `index.md` and the matching `index-<category>.md` to navigate to topic pages.
2. Treat `<wiki-index-matches>` from the prompt hook as pointers. Run a lookup if there is no hint, if you need more results, or if your agent session does not receive the hook. Use the project as the working directory and pass the actual question as one quoted argument.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lookup.py" --deep -- 'release verification'
   ```

3. Open the linked page before relying on a search result or generated index line. For pages with `<!-- wiki-status v1 -->`, read `## Current status` first and check `As of` against current evidence when the answer depends on freshness. Use `## History` for context and past evidence. For a `Canonical copy`, consult the branch record with `lookup.py --branches`.

If a repository retains an archive in a wiki subdirectory, search it separately when needed and verify any recovered information against current evidence. Waymark scans top-level topic pages.

## Maintain the record

Keep one page per topic and one entry per page in its category index. Update `MEMORY.md` or `index.md` when the category map or router needs to change.

Use a status block for topics that change over time, such as a feature, trial, or release. Generate the [format](reference/format.md) from the project's configuration, then fill in facts and evidence. For a new topic, add a single ordinary index link to give sync the location to update. Edit the page whenever its status changes, move superseded claims into history, and regenerate its index entry and existing front-matter description with sync. Never hand-edit a generated index line.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" template
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" sync topic.md
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" check
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py"
```

A topic page may end with an optional `Where to look` map of pointers into the code (see the [format](reference/format.md)). Write it by hand; `map.py template` prints a skeleton and `map.py suggest` drafts bullets from Git history. On a status page place it after `<!-- history -->`.

Keep legacy reference pages as plain topic pages with ordinary index entries. When an audit identifies a problem, consult the [finding codes](reference/audit-codes.md) and correct the relevant evidence, links, or source fields.
