# Changelog

## [0.1.5]

- The prompt hook escapes every `<` and `>` in page- and index-derived text, so wiki content can no longer close its wrapper or imitate harness tags such as `</system-reminder>`. Only the hook's own wrapper lines stay literal, and the 8000-character cap applies after escaping.
- Words inside a query identifier no longer also count as keywords: `PROJ-12` stops matching a page that only mentions `PROJ-123` through the leftover word `proj`, in both lookup and the hook.
- `status.py sync` writes the front-matter `description` as valid YAML, double-quoting values such as `Cache: invalidation` and escaping `\` and `"`. `check` compares the parsed value, so valid plain or quoted descriptions stay in sync while invalid, unquoted, or multi-line ones are reported and replaced as a whole.
- `audit.py --no-git` runs the format checks that need no Git: `Tested` child bullets with a date and `@`, a `Published` tag's date and evidence pointer, backticked `Merged` refs, the `Canonical copy` shape, and the `W2` void or stale marker.

## [0.1.4]

- Added the optional `merged_label` setting: the `Merged` value meaning "in `main_ref`" can be renamed (for example `staging`) so blocks, index lines, lookup output, and hook previews no longer say `merged main` for code that only reached another branch. With a label configured, a block that still writes `main` fails `status.py check` and audit `S3` with a fix hint, and audit `S6` checks the label against `main_ref`.

## [0.1.3]

- The prompt hook injects less text: a word-only match also needs a keyword that is not common across the index (`WAYMARK_HOOK_MAX_DF`), it shows 3 shorter previews by default, status previews list only informative fields, and the header is one short line.

## [0.1.2]

- The prompt hook ignores turns Claude Code starts itself (task notifications, subagent and session messages) instead of matching their boilerplate.

## [0.1.1]

- Recognize issue-tracker keys as exact identifiers; the prompt hook matches them through index lines, while lookup also searches page bodies.

## [0.1.0]

- Added a Claude Code plugin with prompt-time wiki matching and skills for wiki lookup, audits, and status synchronization.
- Added standalone Python commands to search the wiki, check status evidence against Git, and generate or sync status pages.
- Added per-project configuration, an example wiki, format and audit references, and an offline test suite.
