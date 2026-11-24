#!/usr/bin/env bash
# Open (or update) a bot PR from staged git changes and enable auto-merge (squash).
#
# Usage:
#   GH_TOKEN="$DATA_BOT_TOKEN" bash scripts/open_automerge_data_pr.sh \
#     <branch-name> <commit-message> <pr-title> [pr-body]
#
# Guarantees:
# - Fails loudly (exit 1) if GH_TOKEN is missing or empty.
# - Exits 0 cleanly if no staged changes exist.
# - Strips [skip ci] from commit messages so ci.yml runs and reports required checks.
# - Reuses an already-open PR on <branch-name> instead of piling up duplicate PRs.
# - Enables GitHub auto-merge (--auto --squash --delete-branch) so main's ruleset
#   gates every data update through the same 5 required CI checks as code PRs.
set -euo pipefail

BRANCH="${1:?branch name required}"
RAW_MSG="${2:?commit message required}"
PR_TITLE="${3:?PR title required}"
PR_BODY="${4:-Automated scheduled data update. Auto-merge enabled; merges once required CI checks pass.}"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "::error::DATA_BOT_TOKEN (GH_TOKEN) is not configured — cannot open auto-merge data PR." >&2
  exit 1
fi

if git diff --cached --quiet; then
  echo "No staged changes — skipping PR."
  exit 0
fi

# Never include [skip ci] in PR commits: ci.yml must run so required checks report.
COMMIT_MSG="$(printf '%s' "$RAW_MSG" | sed -E 's/\[skip ci\]//g; s/[[:space:]]+/ /g; s/^ //; s/ $//')"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git checkout -B "$BRANCH"
git commit -m "$COMMIT_MSG"
git fetch origin "$BRANCH" 2>/dev/null || true
git push --force-with-lease origin "HEAD:refs/heads/$BRANCH"

PR_URL="$(gh pr list --head "$BRANCH" --base main --state open --json url --jq '.[0].url // empty')"
if [ -z "$PR_URL" ]; then
  PR_URL="$(gh pr create --base main --head "$BRANCH" --title "$PR_TITLE" --body "$PR_BODY")"
  echo "Opened data PR: $PR_URL"
else
  echo "Updated existing data PR: $PR_URL"
fi

gh pr merge "$PR_URL" --auto --squash --delete-branch
echo "Auto-merge enabled on $PR_URL"
