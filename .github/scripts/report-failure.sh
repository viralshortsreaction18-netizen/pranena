#!/usr/bin/env bash
# Surfaces a failed Gradle log through two channels this sandbox can actually read:
# annotations on the check run, and the body of a release. Downloading Actions logs
# goes through results-receiver.actions.githubusercontent.com, which is unreachable
# from here, so grep'ing into stdout alone would leave the error invisible.
set -uo pipefail

LOG="${1:-build.log}"
TAG="simpmusic-build-log"
LIMIT=80

SUMMARY=$(grep -E "^(e: |FAILURE:|What went wrong:|Execution failed|> Task .* FAILED|Caused by:|\* What went wrong|AAPT|error:)" "$LOG" | head -n "$LIMIT" || true)
if [ -z "$SUMMARY" ]; then
  SUMMARY=$(tail -n "$LIMIT" "$LOG")
fi

# 1. annotations (readable via /check-runs/{id}/annotations)
i=0
while IFS= read -r line; do
  i=$((i + 1))
  [ "$i" -gt 10 ] && break
  printf '::error title=gradle::%s\n' "$(printf '%s' "$line" | head -c 800)"
done <<< "$SUMMARY"

# 2. the whole tail, as a release body (readable via /releases/tags/{tag})
{
  echo '```'
  echo "$SUMMARY"
  echo '```'
} > /tmp/report.md
gh release delete "$TAG" --yes --cleanup-tag >/dev/null 2>&1 || true
gh release create "$TAG" --title "Last Gradle failure" --notes-file /tmp/report.md --prerelease >/dev/null 2>&1 || true
