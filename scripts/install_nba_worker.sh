#!/bin/sh
# Install the daily NBA model run + grade job on this Mac (launchd).
#
# Why local: stats.nba.com blocks datacenter IPs (GitHub Actions), so the
# NBA model can only fetch box scores from a home connection. Once a day at
# 17:45 local time (after the Kalshi/FanDuel slate refreshes) it runs
# scripts/nba_model_local_worker.py --sync-main --push in a dedicated worktree
# (~/.local/share/propstein/nba-worktree, reset to origin/main), which:
#   1. fetches box scores through yesterday,
#   2. grades yesterday's research (viewer/feeds/grades/),
#   3. builds today's research with fresh ESPN availability,
#   4. commits viewer/feeds/nba_model + viewer/feeds/grades and pushes to main.
#
#   sh scripts/install_nba_worker.sh            # install / update
#   sh scripts/install_nba_worker.sh --uninstall
#   tail -f ~/Library/Logs/propstein-nba-worker.log
set -eu
LABEL=com.propstein.nba-worker
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC_REPO="$(cd "$(dirname "$0")/.." && pwd)"
# The job runs in its own worktree on origin/main, never in your working copy.
REPO="$HOME/.local/share/propstein/nba-worktree"
VENV="$HOME/.local/share/propstein/nba-venv"
LOG="$HOME/Library/Logs/propstein-nba-worker.log"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  git -C "$SRC_REPO" worktree remove --force "$REPO" 2>/dev/null || true
  echo "Removed $LABEL and its worktree (the Python environment in $VENV was left in place)."
  exit 0
fi

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
mkdir -p "$(dirname "$VENV")" "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
if [ ! -d "$REPO/.git" ] && [ ! -f "$REPO/.git" ]; then
  git -C "$SRC_REPO" fetch -q origin main
  git -C "$SRC_REPO" worktree add -q --detach "$REPO" origin/main
fi
uv venv -q -p 3.12 --allow-existing "$VENV"
uv pip install -q -p "$VENV/bin/python" -r "$REPO/requirements.lock"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$REPO/scripts/nba_model_local_worker.py</string>
    <string>--sync-main</string>
    <string>--push</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: daily at 17:45 local. Log: $LOG"
echo "Run once now:  launchctl kickstart gui/$(id -u)/$LABEL"
