#!/usr/bin/env bash
# Commit staged changes to data/<workflow>-<UTC>, push using a one-shot
# Authorization header (never persisted in .git/config), open a labeled
# `data-bot` PR via GH_TOKEN (DATA_BOT_TOKEN), and enable auto-merge (squash).
#
# Usage:
#   GH_TOKEN="$DATA_BOT_TOKEN" bash scripts/open_automerge_data_pr.sh \
#     <workflow-slug> <commit-message> [pr-body]
#
# Guarantees:
# - Never prints, echoes, or persists GH_TOKEN. Never runs with `set -x`.
# - Fails loudly (exit 1) if FORCE_FAIL_PR_STEP=true or GH_TOKEN is missing.
# - Exits 0 cleanly if no staged changes exist (idempotent).
# - Strips [skip ci] from commit messages so ci.yml runs required checks.
# - Enables GitHub auto-merge (--auto --squash --delete-branch).
set -euo pipefail

WORKFLOW_SLUG="${1:?workflow slug required}"
RAW_MSG="${2:?commit message required}"
PR_BODY="${3:-Automated scheduled data update (${WORKFLOW_SLUG}). Auto-merge enabled; merges once required CI checks pass.}"

if [ "${FORCE_FAIL_PR_STEP:-false}" = "true" ]; then
  echo "::error::Forced PR step failure (force_fail_pr_step=true)." >&2
  exit 1
fi

if [ -z "${GH_TOKEN:-}" ]; then
  echo "::error::DATA_BOT_TOKEN (GH_TOKEN) is not configured — cannot open auto-merge data PR." >&2
  exit 1
fi

if git diff --cached --quiet; then
  echo "No staged changes for ${WORKFLOW_SLUG} — skipping PR."
  exit 0
fi

# Strip [skip ci] so ci.yml runs and reports the 5 required status checks.
COMMIT_MSG="$(printf '%s' "$RAW_MSG" | sed -E 's/\[skip ci\]//g; s/[[:space:]]+/ /g; s/^ //; s/ $//')"

UTC_TS="$(date -u +%Y%m%dT%H%M%SZ)"
UTC_DAY="$(date -u +%F)"
BRANCH="data/${WORKFLOW_SLUG}-${UTC_TS}"
PR_TITLE="data(${WORKFLOW_SLUG}): ${UTC_DAY}"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git checkout -B "$BRANCH"
git commit -m "$COMMIT_MSG"

# Scoped one-shot auth header for this single git push — never written to .git/config.
AUTH_B64="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${AUTH_B64}" \
  push origin "HEAD:refs/heads/${BRANCH}"
unset AUTH_B64

PR_URL="$(gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "$PR_TITLE" \
  --body "$PR_BODY" \
  --label "data-bot")"
echo "Opened data-bot PR: ${PR_URL}"

gh pr merge "$PR_URL" --auto --squash --delete-branch
echo "Auto-merge enabled on ${PR_URL}"
