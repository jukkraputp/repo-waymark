---
name: sync
description: Use when the user explicitly asks to regenerate Repo Waymark wiki index entries and descriptions from topic status blocks.
argument-hint: '[page.md ...]'
disable-model-invocation: true
---

# Sync generated wiki entries

Run sync in the project checkout. Pass the requested topic filenames as separate arguments. With no filenames, sync processes all opted-in pages. Use `--root DIR` if you need to select another project.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" sync topic.md
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" check
```

The source page needs a valid marked block and exactly one index link pointing to it. Sync replaces that link line with the generated status entry and updates an existing front-matter `description` from `About`. It preserves `As of` and the rest of the page. See the [status block format](../workflow/reference/format.md) when a topic cannot sync.

Report updated, already synced, and skipped pages. Check for drift after the run and review the affected diff. The check scans the whole wiki even after a targeted sync, so identify any remaining drift outside the requested pages separately.
