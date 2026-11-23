# Changelog

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
