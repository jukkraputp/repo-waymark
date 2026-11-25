# Audit findings

The audit reports category counts, index integrity, and then findings on topic pages. With `--strict`, warnings fail the audit alongside errors. Git checks use the locally available refs and tags. The [status block format](format.md) describes the fields referred to below.

| Index finding | Meaning |
| --- | --- |
| `ORPHANS` | A top-level topic page lacks a category index link. |
| `DANGLING` | A category index links to a missing top-level topic page. |
| `DUPES` | A topic page has more than one category index link. |
| `BROKEN WIKILINKS` | A slug-shaped `[[wikilink]]` points to a missing topic page. |
| `EXTERNAL (not checked)` | An index entry points outside the top-level topic set. This list is informational. |

## Status errors

| Code | Meaning |
| --- | --- |
| `S1` | Block placement, required fields, or the history marker are invalid. |
| `S2` | `As of` has an invalid or future date, is missing `checked at`, or cites an unavailable commit. |
| `S3` | A status value starts with a token outside its field vocabulary or has an invalid `Published` value. With a non-default `merged_label`, `Merged: main` is reported here (and by `status.py check`) with the label to use. |
| `S4` | The topic lacks exactly one matching index link, the block cannot render, or the index line or description differs from the generated value. |
| `S5` | A cited commit SHA does not exist in the Git checkout used for evidence. If the same SHA is reported by `S2` for `As of`, it is counted once. |
| `S6` | `Merged` names an unresolved or missing ref, lacks the required code commit evidence, or contradicts Git ancestry. The `main` value (or the configured `merged_label`) is checked against `main_ref`. |
| `S7` | The `Published` tag is missing, peels to a different commit than cited, or lacks a date or evidence pointer. |
| `S8` | `Tested` lacks required dated child bullets, an `@` revision or label, or a resolvable backticked SHA or tag. |
| `S9` | `Canonical copy` lacks a backticked `<ref>:<path>` or its ref or project-relative path cannot be resolved. |
| `S10` | A link in the block has a missing wiki or local target, or a resolvable Git ref lacks the cited project-relative path. |
| `S11` | A `Superseded` entry lacks `→` or `replaced by`. |
| `S12` | `Summary` or `About` exceeds its length limit, or the H1 contains a status word. |
| `S13` | A dated history heading is newer than `As of`. |
| `S14` | A field is duplicated, including a required field repeated in a child bullet. |
| `S15` | A required field uses a noncanonical spelling or capitalization. |
| `S16` | A line in the block cannot be parsed. |
| `S17` | The opted-in or indexed status block is missing or invalid, or a generated entry has no readable page. |
| `S18` | A `Where to look` map is malformed: no heading after the marker, a bullet without a backticked pointer or ` — role`, no bullets or more than 12, a second map section, a bullet marker other than `- `, or, on a status page, a map placed before `<!-- history -->`. |

## Warnings

| Code | Meaning |
| --- | --- |
| `W1` | `As of` is more than 14 days old. |
| `W2` | A tested revision predates newer code or a test entry explicitly marks the result stale or void. |
| `W3` | The cited code is already in the target ref despite a negative merge claim or an integration-only claim. |
| `W4` | The canonical ref advanced from the cited tip or is now an ancestor of the current checkout's `HEAD`. |
| `W5` | Git does not track the topic page. |
| `W6` | A status block cites a missing working-tree repository path. |
| `W7` | A map pointer is not a regular file at `main_ref` (or at its own `ref:`), its ref does not resolve, or `main_ref` does not resolve so plain pointers were not verified. |
| `W8` | A map pointer carries a line number (`:12`), or its symbol's last segment is not found as a whole word in the file. |
| `G1` | A topic page has a broken relative Markdown link. |
| `G2` | A value matched by the configured release tag pattern has no corresponding Git tag. |
| `GIT` | Git checks were unavailable. An explicit `--no-git` run instead reports the skip directly. Format checks that need no Git (`S6` backticked refs, `S7` date and evidence pointer, `S8` dated child bullets with `@`, `S9` the `<ref>:<path>` shape, and the `W2` void or stale marker) still run; SHA, ref, tag, and ancestry checks do not. |
| `UTF8` | An index or topic has invalid UTF-8 bytes. The audit reads replacement characters for those bytes and continues checking the file. |

For backticked working-tree paths, `W6` checks a slash-separated token when its first component exists in the selected checkout or it has a recognized file extension. A token ending in `/` needs an existing first component. For Git paths, the selected project directory is `--root` or the discovered wiki root, with `WAYMARK_GIT_ROOT` supplying a separate checkout location when set.

`W7` and `W8` read files with read-only Git (`ls-tree`, `cat-file`) at the named commit, never the working tree. Only regular files count; a directory, symlink, or submodule gives `W7`. The symbol check skips files over 1 MB. Map pointers are excluded from the `G2` release tag scan.

`CONFIG` reports unusable configuration and exits `2`. For `S4`, repair missing or duplicate index links first, then run sync. For evidence findings, check the cited facts before updating the page or its `As of` date.
