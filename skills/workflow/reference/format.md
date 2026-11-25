# Wiki and status block format

## Layout

`MEMORY.md` is the category map and `index.md` can route readers to individual `index-<category>.md` files. Each category index lists its topic pages with Markdown bullets such as `- [Release checks](release-checks.md)`. Topics are top-level Markdown files under the wiki directory. Keep exactly one index bullet per topic. Use `[[release-checks]]` to link to another topic by its filename stem.

Plain topics can keep ordinary index bullets with a short description. A status block is useful when readers need to distinguish the current state of a feature, release, or other changing topic from its history. The marker opts the page in. A `## Current status` heading on a legacy page can remain ordinary prose.

## Generate and populate the block

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" template
```

Use a neutral H1. Put `<!-- wiki-status v1 -->` directly below it, allowing blank lines, then `## Current status`. Use a top-level bullet of the form `- **Field:** value` with canonical field spelling for each field. Indent child bullets and wrapped text by at least two spaces. End the block with `<!-- history -->` and place older material under `## History`.

| Field | What to record |
| --- | --- |
| `As of` | Date the claims were verified, in `YYYY-MM-DD` format, followed by `checked at` and a backticked Git commit SHA. Name the ref inspected when relevant. |
| `Summary` | Current status in one sentence, up to 200 characters. |
| `About` | A stable topic description, up to 160 characters. |
| `Decision` | `proposed`, `approved`, `trial`, `rejected`, `superseded`, `n/a`, or `unknown`, plus evidence. |
| `Code` | `none`, `uncommitted`, `partial`, `committed`, `n/a`, or `unknown`. Backtick any Git commit SHA cited as evidence. |
| `Merged` | `no`, `integration`, `main`, `partial`, `n/a`, or `unknown`. `main` means the code is in the configured `main_ref`; when the project sets `merged_label` (for example `staging`), write that label instead of `main`, which is then invalid. For `no`, `integration`, `main` (or the label), and `partial`, name the actual checked ref or refs in backticks. For `integration` and `main` (or the label), cite the commits under `Code`. |
| `Tested` | `none`, `partial`, `passed`, `failed`, or `unknown`. For `partial`, `passed`, or `failed`, add dated child bullets with `@` followed by a backticked SHA or tag, an unquoted environment label, or `unknown`. Include the observed result and its evidence. |
| `Unverified` | Claims or steps still missing evidence, or `none known`. |
| `Published` | `no`, `n/a`, `unknown`, or a backticked Git tag followed by a date and evidence pointer. If you cite the tag's target with `→`, backtick the commit SHA. |
| `Next` | Next action or blocker, including an owner if known. |
| `Superseded` | `none`, or replacements written as `old claim → new claim` or `old claim replaced by new claim`. |
| `Keywords` | Comma-separated terms people may search for, including plain-language aliases and exact identifiers such as issue keys `ABC-123`. |
| `Canonical copy` | Optional backticked `<ref>:<path>` pointer for a topic whose authoritative record is on another branch. |

The generated template includes any required project-specific fields. Keep decision, implementation, merge, test, and publication evidence tied to their actual revisions. Use `unknown` when a status or test revision has no evidence. For historical text that can mislead, add a dated banner such as `> **Superseded YYYY-MM-DD:** old claim → replacement (see Current status).`

After editing the source, run `status.py sync` on the page to regenerate the category index line and any existing front-matter `description`. The description is written on one line as a YAML scalar, double-quoted when `About` would not read back as plain YAML (for example `Cache: invalidation`); an existing multi-line or invalid description is replaced as a whole. Sync leaves `As of` alone. Update that date only after verifying the status claims.

## Where to look map (optional)

Any topic page, with or without a status block, may end with a short map of pointers into the code. A person writes it; no tool edits it. On a status page the map goes after `<!-- history -->`, because the status block ends there.

```markdown
<!-- wiki-map v1 -->
## Where to look

- `lib/app.dart` `App.start` — entry point
- Trap: `lib/old_client.dart` — the legacy client; use `lib/client.dart`
- `feature/x:lib/new.dart` — unmerged change, on its branch only
```

Rules: `<!-- wiki-map v1 -->` is followed by the heading `## Where to look`; one map per page; 1 to 12 bullets, each starting with a backticked pointer, an optional backticked symbol, and ` — ` plus a role. Prefix `Trap: ` for a file that looks right but is wrong. A pointer is a path relative to the project, resolved at `main_ref`; write `ref:path` for a file that exists only on another branch. Name a symbol instead of a line number. Audit checks the map with `S18`, `W7` and `W8`. Lookup prints it, and the prompt hook adds up to three `where:` or `trap:` lines in map order, so put the most important bullets first. `python3 scripts/map.py template` prints a skeleton and `map.py suggest TERM...` drafts bullets from the files that commits mentioning the term touched.
