---
name: audit
description: Use when the user explicitly asks to audit a Repo Waymark wiki for index, link, status format, or evidence issues.
argument-hint: '[--strict] [--no-git]'
disable-model-invocation: true
---

# Audit the wiki

Run the read-only audit in the project checkout. Pass `--root DIR` to select a different project, `--strict` to include warnings in the failure exit code, or `--no-git` when an audit without Git evidence is requested.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py"
```

Report the index and link findings, status errors and warnings, and whether Git checks ran. Map findings are `S18` (malformed map), `W7` (pointer is not a file) and `W8` (line number or missing symbol). Use the [finding codes](../workflow/reference/audit-codes.md) to explain the cause and name the affected files. Cross-check repository evidence when the output indicates stale claims. An audit request by itself calls for a report. Apply fixes when the user requests them.

Exit `0` means no failing findings, `1` means failing findings, and `2` means a usage, configuration, or internal error. Warnings can appear with exit `0`. When Git is unavailable or skipped, report the narrower verification scope.
