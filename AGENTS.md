# Contributors

Repo Waymark is a public MIT-licensed Claude Code plugin and dependency-free Python toolkit. Keep examples and fixtures generic, with synthetic repository names, refs, and evidence. Keep personal paths, private repository details, credentials, account data, and project-local settings out of tracked files.

## Development

- Keep the CLI compatible with Python 3.9 and the Python standard library. Use Git through read-only commands when checking evidence. Limit scripted wiki edits to status synchronization.
- Use the Current status block as the source for generated index entries and existing front-matter descriptions. Preserve unrelated wiki content and keep the prompt hook quiet on empty results and errors.
- Update the README, the workflow references, and the example wiki when changing their documented interfaces. Keep the audit code reference aligned with the CLI.
- Run `python3 -m unittest discover -s tests -v` from the repository root. Tests that exercise Git use temporary repositories and local refs.

## Releases

Update the version in `.claude-plugin/plugin.json` and add a matching entry in `CHANGELOG.md`. Run the test suite and validate the plugin and marketplace manifests before tagging the release with the matching `vX.Y.Z` tag.
