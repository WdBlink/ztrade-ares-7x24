#!/usr/bin/env bash
# ares-up.sh — install launchd plist + start conductor (PRD §17, §4.2)
set -euo pipefail

PROJECT_ROOT="${1:-$(pwd)}"
PLIST_NAME="com.wdblink.ztrade-ares-7x24"
PLIST_SRC="$PROJECT_ROOT/bin/${PLIST_NAME}.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [ ! -f "$PLIST_SRC" ]; then
  echo "no plist template at $PLIST_SRC" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

# Substitute project root into the plist
sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$PLIST_SRC" > "$PLIST_DEST"

# Install the cron tick (1-minute)
( crontab -l 2>/dev/null | grep -v 'ares-cron-tick.sh' || true
  echo "* * * * * $PROJECT_ROOT/bin/ares-cron-tick.sh"
) | crontab -

# Start (or refresh) launchd job
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo "launchd plist installed: $PLIST_DEST"
echo "cron tick installed: every 1 minute -> $PROJECT_ROOT/bin/ares-cron-tick.sh"

# Run the init + up sequence once now
cd "$PROJECT_ROOT"
ar724 init
ar724 up
