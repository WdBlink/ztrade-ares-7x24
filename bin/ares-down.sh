#!/usr/bin/env bash
# ares-down.sh — graceful shutdown (PRD §17)
set -euo pipefail

PLIST_NAME="com.wdblink.ztrade-ares-7x24"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

# Unload launchd
if [ -f "$PLIST_DEST" ]; then
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Remove cron entry
( crontab -l 2>/dev/null | grep -v 'ares-cron-tick.sh' || true ) | crontab -

# Stop the controller and tmux session
ar724 down || true

echo "ar724 stopped (launchd unloaded, cron removed, tmux killed)"
