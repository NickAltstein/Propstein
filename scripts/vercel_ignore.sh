#!/usr/bin/env bash
# Vercel: exit 0 to skip the build, exit 1 to build. Missing shallow-clone
# history must build, never abort deployment with git's exit code 128.
set -u

if [ "${VERCEL_ENV:-}" = "preview" ]; then
  echo "Skipping preview Vercel build (VERCEL_ENV=preview)."
  exit 0
fi

base="${VERCEL_GIT_PREVIOUS_SHA:-HEAD^}"
if ! git rev-parse --verify --quiet "${base}^{commit}" >/dev/null 2>&1; then
  echo "Vercel previous commit unavailable locally; building to avoid a stale deployment."
  exit 1
fi

if git diff --quiet "$base" HEAD -- \
  viewer/ public/ api/ src/ requirements.txt package.json vercel.json \
  ':(exclude)viewer/news_watcher_state.json' \
  ':(exclude)public/news_watcher_state.json'; then
  echo "No deployed paths changed; skipping Vercel build."
  exit 0
fi

echo "Deployed paths changed or diff unavailable; building."
exit 1
