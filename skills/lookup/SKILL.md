---
name: lookup
description: Use when looking up a question, topic, exact identifier, version, or commit in a repository's Repo Waymark wiki.
argument-hint: <question or identifier>
---

# Look up wiki records

Run lookup in the project checkout. Use the actual question or identifier from `$ARGUMENTS` as one literal argument after `--`. Quote it for the shell so the whole query reaches Python unchanged. If no query was supplied, ask for the question or identifier. Use `--root DIR` before `--` if you need to select a different project root.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lookup.py" -- 'ReleaseManifest'
```

| Option | Use |
| --- | --- |
| `--deep` | Rank topic page bodies by keyword when there are fewer than three index hits. Strong exact identifiers already get body matching. |
| `--branches` | Read a hit's `Canonical copy` at the locally resolved commit without switching branches. |
| `--max N` | Limit index hits, which default to five. |
| `--blocks all` | Print status blocks and `Where to look` maps for all shown hits in text output. The default prints up to three of each. |
| `--json` | Return structured results. |

Open the best matching page and check the dated status block and its evidence. A `Where to look` map lists the files to open first; a `Trap:` bullet names a file that looks right but is wrong. Pointers are checked against `main_ref` when audited, so confirm a file exists before relying on it. Include the page or evidence location in the answer. Exit `0` means results were found, `1` means no results, and `2` means a usage, configuration, or internal error. A search miss can be worth retrying with an exact identifier or a different keyword.
