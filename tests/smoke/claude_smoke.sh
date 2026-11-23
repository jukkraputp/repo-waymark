#!/usr/bin/env bash
# Manual integration check; requires an authenticated Claude Code installation.
set -u
repo=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
failed=0
if claude plugin validate "$repo" --strict; then
  echo 'PASS: plugin validation'
else
  echo 'FAIL: plugin validation'; failed=1
fi
if claude --plugin-dir "$repo" plugin list --json > "$tmp/plugins.json" && grep -q 'repo-waymark' "$tmp/plugins.json"; then
  echo 'PASS: plugin list'
else
  echo 'FAIL: plugin list'; failed=1
fi
cp -R "$repo/templates/example-wiki/." "$tmp/"
prompt='what is the status of the example topic'
if python3 "$repo/scripts/prompt_hook.py" --root "$tmp" "$prompt" > "$tmp/hook.out" &&
  grep -Fq '<wiki-index-matches>' "$tmp/hook.out" &&
  grep -Fq 'Example topic' "$tmp/hook.out"; then
  echo 'PASS: prompt hook output'
else
  echo 'FAIL: prompt hook output'; failed=1
fi
# Print mode makes a model call and can also run other configured hooks.
if (cd "$tmp" && claude --plugin-dir "$repo" --debug-file "$tmp/debug.log" -p "$prompt") &&
  grep -Fq 'UserPromptSubmit' "$tmp/debug.log" &&
  grep -Eq 'prompt_hook[.]py.*finished with status 0([^[:digit:]]|$)' "$tmp/debug.log"; then
  echo 'PASS: prompt hook trace'
else
  echo 'FAIL: prompt hook trace'; failed=1
fi
exit "$failed"
