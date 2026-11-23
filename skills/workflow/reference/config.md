# Project configuration

Add `.repo-waymark.json` at the project root when it needs to override the defaults. Omitted keys keep their defaults.

| Key | Default | Meaning |
| --- | --- | --- |
| `wiki_dir` | `.claude/memory` | Relative path within the project to the wiki directory. |
| `main_ref` | `origin/main` | Git reference used to verify mainline merge claims. |
| `extra_fields` | `{}` | Project-specific status fields keyed by the name used in the block. Each field has a nonempty `vocab` of single tokens, a `label` for the generated index, and an optional `omit_when` token. A configured field remains required in the block even if its current token omits it from the index. |
| `release_tag_pattern` | `null` | Python regular expression that matches complete release tag strings in topic text. A match without a Git tag raises `G2`. The default disables this scan. |
| `merged_label` | `main` | The `Merged` value that means "in `main_ref`". Lowercase, matching `[a-z0-9][a-z0-9._/-]{0,31}`, not ending in `.`, and not one of `no`, `integration`, `partial`, `n/a`, or `unknown`. With a non-default label, a block that writes `Merged: main` is invalid (`S3`) until it uses the label. |

```json
{
  "wiki_dir": "docs/wiki",
  "main_ref": "origin/trunk",
  "extra_fields": {
    "Approval": {
      "vocab": ["pending", "granted", "unknown"],
      "label": "approval",
      "omit_when": "unknown"
    }
  },
  "release_tag_pattern": "\\bv[0-9]+\\.[0-9]+\\.[0-9]+\\b"
}
```

A team that merges into `staging` and releases to `main` later can name that stage instead of `main`:

```json
{
  "main_ref": "origin/staging",
  "merged_label": "staging"
}
```

Blocks then write ``- **Merged:** staging — in `origin/staging`, not in `origin/main` ``, and index lines, lookup output, and hook previews show `merged staging`. `status.py template` prints the vocabulary with the configured label.

The CLI resolves the project from `--root DIR`, then `WAYMARK_ROOT`. Without either, it walks up from `CLAUDE_PROJECT_DIR` or the working directory, stopping at a Git checkout boundary. Hooks start at the hook input's `cwd` instead, which follows Claude into a worktree, and fall back to `CLAUDE_PROJECT_DIR` when it has no wiki. Discovery looks for `.repo-waymark.json` or a default wiki with category indexes. Set `WAYMARK_GIT_ROOT` to use a separate local checkout for Git evidence, such as when working on an exported wiki.

The prompt hook supports `WAYMARK_HOOK_MIN_KEYWORDS` (default `2`), `WAYMARK_HOOK_MAX_DF` (`0.03`), `WAYMARK_HOOK_MAX_HITS` (`3`), `WAYMARK_HOOK_ENTRY_CHARS` (`160`), and `WAYMARK_HOOK_STATUS_CHARS` (`220`). These tune the hook's suggestions. A keyword on more than the `WAYMARK_HOOK_MAX_DF` share of index lines (and on more than 3 lines) is common: a word-only hook match also needs at least one keyword that is not common. CLI lookup can qualify a result with one matching keyword.
