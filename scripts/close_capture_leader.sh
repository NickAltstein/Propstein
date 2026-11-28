#!/usr/bin/env bash
# Decide whether this close-quality capture run leads the pre-game session.
#
# Several triggers start this workflow (Vercel Schedules every 15 minutes,
# GitHub's cron, the news-watcher backstop, a session's own handoff). The
# session (src/close_quality_session.py) stays up through every game's
# pre-game window, so only one run should hold it: the oldest active run on
# main leads and every newer run exits at once. A successor started by a
# session's handoff ignores the run that dispatched it (HANDOFF_FROM), which
# is still finishing its last data PR.
#
# If the GitHub API cannot be read, the run leads (fail open): two sessions
# capture the same markets twice, which is better than none capturing.
#
# Writes lead=true|false to $GITHUB_OUTPUT. Needs GH_TOKEN with actions:read.
set -euo pipefail

: "${GITHUB_REPOSITORY:?}" "${GITHUB_RUN_ID:?}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"
HANDOFF_FROM="${HANDOFF_FROM:-}"
API="repos/${GITHUB_REPOSITORY}/actions/workflows/close_quality_capture.yml/runs?branch=main&per_page=100"

older=""
for status in in_progress queued; do
  if ! ids=$(gh api "${API}&status=${status}" --jq '.workflow_runs[].id'); then
    echo "::warning::Could not list ${status} close-capture runs; leading the session (fail open)."
    echo "lead=true" >> "$OUT"
    exit 0
  fi
  for id in $ids; do
    if [ "$id" -lt "$GITHUB_RUN_ID" ] && [ "$id" != "$HANDOFF_FROM" ]; then
      older="$id"
    fi
  done
done

if [ -n "$older" ]; then
  echo "Run ${older} already holds the pre-game session; this run exits."
  echo "lead=false" >> "$OUT"
else
  echo "No older active run; this run leads the pre-game session."
  echo "lead=true" >> "$OUT"
fi
