# repo-waymark

Keep repository status in Markdown and find it from Claude Code or the command line. A dated **Current status** block records decisions, code, merges, tests, publication, and next steps. Waymark generates the topic's index entry and updates its existing front-matter description from that block. The audit checks the wiki's structure and compares cited evidence with local Git data.

## Install

Use Python 3.9 or later with `python3` on your PATH. The scripts use the Python standard library. Install Git for repository evidence checks and install Claude Code to use the plugin. macOS and Linux are supported. Windows is untested.

Run these commands from the project where you want to enable Waymark.

```sh
claude plugin marketplace add jukkraputp/repo-waymark
claude plugin install repo-waymark@repo-waymark --scope project
```

Use project scope and enable the plugin only in repositories you trust. The hook runs at each prompt wherever the plugin is enabled and adds matching repository wiki text to Claude's model context. It stays quiet when the project has no wiki or the prompt has no qualifying matches. Start a new Claude Code session or run `/reload-plugins` in the current session. Manage the installed plugin from `/plugin`. [Claude Code plugin installation and scopes](https://code.claude.com/docs/en/discover-plugins)

To try a local copy for one session, clone the plugin and launch Claude Code from your project with the checkout path.

```sh
git clone https://github.com/jukkraputp/repo-waymark.git
claude --plugin-dir /path/to/repo-waymark
```

## Try the example wiki

Run this block from a local repo-waymark checkout. It copies [the example wiki](templates/example-wiki/) into a temporary project, syncs its index, and runs a lookup and audit.

```sh
waymark="$PWD"
project="$(mktemp -d)"
cp -R "$waymark/templates/example-wiki/." "$project/"
python3 "$waymark/scripts/status.py" --root "$project" sync
python3 "$waymark/scripts/status.py" --root "$project" check
python3 "$waymark/scripts/lookup.py" --root "$project" "example topic"
python3 "$waymark/scripts/audit.py" --root "$project" --no-git
```

When adopting the template, copy its files into a fresh project or merge them into your existing wiki and configuration. Before running the Git-backed audit, replace the sample `As of` date with today's date (`date +%F`) and the placeholder SHA with a real commit from the project (`git rev-parse --short HEAD` if you checked `HEAD`). Remove the illustrative placeholder note, record your own evidence, and run `status.py sync` before auditing.

For each status topic, place a top-level Markdown file in the wiki directory and give it exactly one bullet link in an `index-*.md` file. Use `status.py template` to generate the Markdown skeleton for your configuration. Put `<!-- wiki-status v1 -->` and `## Current status` immediately beneath the H1, and keep previous records below `<!-- history -->`. Edit the block, update its `As of` date when you reverify the evidence, and run `status.py sync` to regenerate derived text. [Status block format](skills/workflow/reference/format.md)

```markdown
# Example topic

<!-- wiki-status v1 -->
## Current status

- **As of:** 2026-01-01 · checked at `1abc234` (illustrative placeholder)
- **Summary:** The example topic is awaiting review.
- **About:** A neutral example of a status page.
- **Decision:** unknown
- **Code:** unknown
- **Merged:** unknown
- **Tested:** unknown
- **Unverified:** All implementation and review evidence.
- **Published:** unknown
- **Next:** Review the evidence and update this page.
- **Superseded:** none
- **Keywords:** example, topic, review, status, evidence, sample, guide, reference, ABC-123

<!-- history -->
## History

2026-01-01: Created the example page.
```

## In Claude Code

| Feature | How to use it |
| --- | --- |
| Prompt hook | Submit a prompt. The hook surfaces matching topic pointers and dated previews from the wiki. |
| Workflow skill | Claude loads the wiki workflow when relevant to a status task. |
| Lookup | `/repo-waymark:lookup <question or identifier>` |
| Audit | `/repo-waymark:audit [--strict] [--no-git]` |
| Sync | `/repo-waymark:sync [page.md …]`. Omit page names to sync the wiki. |

## CLI and other agents

Run the scripts from your project, or pass the project root explicitly when the scripts live in another checkout. Any coding agent with shell access can call the scripts directly. For example, an agent can search a project wiki with the following command. The [workflow skill](skills/workflow/SKILL.md) describes how to read and update the records.

```sh
python3 /path/to/repo-waymark/scripts/lookup.py --root /path/to/project "example topic"
```

| Command | Behavior |
| --- | --- |
| `python3 scripts/status.py [--root DIR] check` | Check opted-in pages and report stale index entries or descriptions. `check` is the default subcommand. |
| `python3 scripts/status.py [--root DIR] sync [page.md …]` | Update linked index lines and existing front-matter descriptions for the named pages, or all opted-in pages. |
| `python3 scripts/status.py [--root DIR] template` | Print a status page skeleton, including configured extra fields. |
| `python3 scripts/lookup.py [--root DIR] [OPTIONS] QUERY` | Rank matching wiki entries and find exact identifiers in page bodies. |
| `python3 scripts/audit.py [--root DIR] [--strict] [--no-git]` | Check wiki links, status blocks, and Git evidence. `--strict` makes warnings fail the audit. `--no-git` skips Git checks. |
| `python3 scripts/prompt_hook.py [--root DIR] [PROMPT]` | Preview hook output. Pass prompt words as arguments, or send a JSON object with a `prompt` field or plain prompt text on stdin. |

Lookup accepts `--max N` to cap index hits (default 5), `--json` for structured output, and `--blocks all|top` to show status blocks for all displayed status hits in text output or up to three (default `top`). `--branches` reads the `Canonical copy` at its resolved commit with Git. `--deep` adds body keyword matches when there are fewer than three index hits.

Issue keys such as `ABC-123` are exact identifiers. The prompt hook matches them only via index lines, so list them in a topic's `Keywords` field; lookup also searches page bodies.

| Exit | Status | Lookup | Audit |
| --- | --- | --- | --- |
| `0` | Template printed, check clean, or sync succeeded | Matches returned | Audit passed |
| `1` | Check found problems, or sync could not update some requested pages | No matches or empty query | Findings, including warnings with `--strict` |
| `2` | Usage, configuration, or runtime error | Usage, configuration, or runtime error | Usage, configuration, or runtime error |

The prompt hook exits `0` with or without matches and silently handles errors. Its output is capped at 8,000 characters. Read the linked topic before using a preview. [Audit code reference](skills/workflow/reference/audit-codes.md)

## Configuration

Add an optional `.repo-waymark.json` to the project root. The example below shows the defaults.

```json
{
  "wiki_dir": ".claude/memory",
  "main_ref": "origin/main",
  "extra_fields": {},
  "release_tag_pattern": null
}
```

| Key | Purpose |
| --- | --- |
| `wiki_dir` | Relative path to the wiki directory inside the project. |
| `main_ref` | Git reference used to verify claims that code has reached the main line. |
| `extra_fields` | Required project-specific status fields. Each field defines a single-token `vocab`, an index `label`, and an optional `omit_when` token that keeps that value out of the generated index. |
| `release_tag_pattern` | Optional regex matching complete tag names cited in page text. During Git audits, matches are checked against local tags. |

`main_ref` defaults to `origin/main`. If your repository has no such remote-tracking ref or uses another default branch, set `main_ref` in `.repo-waymark.json` to the ref you verify against, such as `main` in a local-only repository or `origin/master` for a remote branch named `master`.

Root resolution uses `--root` first, then `WAYMARK_ROOT`. Without an override, discovery starts at `CLAUDE_PROJECT_DIR` when set or the current directory and walks up to the repository boundary. A project is discovered from its config file or a default wiki containing an `index-*.md` file. [Configuration reference](skills/workflow/reference/config.md)

| Environment variable | Default or behavior |
| --- | --- |
| `WAYMARK_ROOT` | Explicit project root when `--root` is omitted. |
| `CLAUDE_PROJECT_DIR` | Starting directory for discovery when set. |
| `WAYMARK_GIT_ROOT` | Separate checkout for audit Git evidence and lookup branch reads. Defaults to the wiki project root. |
| `WAYMARK_HOOK_MIN_KEYWORDS` | `2` distinct keyword matches for a normal prompt hit. Exact identifiers can qualify on their own. |
| `WAYMARK_HOOK_MAX_HITS` | `6` index matches shown by the hook. |
| `WAYMARK_HOOK_ENTRY_CHARS` | `240` characters per plain index preview. |
| `WAYMARK_HOOK_STATUS_CHARS` | `380` characters per status preview. |

## Limits

- The wiki uses top-level Markdown topic files and category indexes named `index-*.md`. Plain indexed pages are searchable. Pages with the status marker opt in to status validation and synchronization.
- Git checks use locally available objects, refs, and tags. The audit can validate cited commits and their ancestry. The underlying test runs, reviews, and deployments still need their own evidence.
- Hook previews use keyword and exact-identifier matching. The CLI accepts single-keyword matches and provides optional body search for broader recall.

## License

[MIT](LICENSE). Project repository [jukkraputp/repo-waymark](https://github.com/jukkraputp/repo-waymark).
