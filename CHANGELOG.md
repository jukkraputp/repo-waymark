# Changelog

## [0.1.7]

- Added the optional "Where to look" map: a short list of pointers into the code at the end of any topic page, introduced by `<!-- wiki-map v1 -->` and `## Where to look` (one to twelve bullets: a backticked pointer, an optional backticked symbol, ` — role`, and a `Trap:` prefix for misleading files; `ref:path` for branch-only files). Lookup prints it for the top three hits (all with `--blocks all`, also in `--json`), and the prompt hook adds up to three indented lines under a hit in the map's own order (`  where: \`path\` \`Symbol\` — role`, or `  trap: \`path\` — role`), within one shared budget `WAYMARK_HOOK_WHERE_CHARS` (default `320`, `0` turns them off). A line that does not fit is dropped whole, never cut.
- Audit checks maps with `S18` (malformed, including a map placed before `<!-- history -->` on a status page), `W7` (pointer is not a regular file at `main_ref` or its own ref) and `W8` (line number, or the symbol's last segment is not a whole word in the file). Map pointers are ignored by the `G2` release tag scan and the `S13` heading scan.
- Added `scripts/map.py` with `template` and `suggest`. `suggest TERM...` finds commits on `main_ref` (or `--ref`) whose message contains a term (fixed string, case-insensitive), diffs each against its first parent, ranks files by matching commits then lines changed, drops files that are not regular files at the ref or are in the wiki directory, and prints ready-to-paste bullets with a `TODO` role plus the commits used. `--commits SHA...` replaces the search and `--branch REF` lists a branch's files as `REF:path`. It prints text and never edits a file.

## [0.1.6]

- Added two vendorable project hooks under `scripts/repo_hooks/`, documented in the README with an exec-form `.claude/settings.json` example. `post_git.py` (`PostToolUse`, `Bash|PowerShell`) reminds Claude that a `git commit`, `git merge`, `git push`, `gh pr create`, or `gh pr merge` may have changed a topic's status. `stop_check.py` (`Stop`) blocks once when `status.py check` finds problems in wiki files with uncommitted changes in this checkout. Both read the wiki directory, `main_ref`, and `merged_label` from the project's configuration and print commands relative to the repository root.
- `post_git.py` sees through `command`, `sudo`, `env`, `time`, and `nohup` prefixes and gh's `-R/--repo` flag, skips `git push --dry-run`/`-n`, `git commit --dry-run`, and `git -C` into a directory outside the repository, and reads PowerShell command text.
- `stop_check.py` stays silent for drift that is already committed, when Git is unavailable, and when the check crashes.
- The prompt hook looks for the wiki from the hook input's `cwd` before `CLAUDE_PROJECT_DIR`, so inside a worktree it previews the same checkout the Stop hook checks. `--root` and `WAYMARK_ROOT` still take precedence.

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
