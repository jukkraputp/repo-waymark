# Changelog

## [0.1.2]

- The prompt hook ignores turns Claude Code starts itself (task notifications, subagent and session messages) instead of matching their boilerplate.

## [0.1.1]

- Recognize issue-tracker keys as exact identifiers; the prompt hook matches them through index lines, while lookup also searches page bodies.

## [0.1.0]

- Added a Claude Code plugin with prompt-time wiki matching and skills for wiki lookup, audits, and status synchronization.
- Added standalone Python commands to search the wiki, check status evidence against Git, and generate or sync status pages.
- Added per-project configuration, an example wiki, format and audit references, and an offline test suite.
