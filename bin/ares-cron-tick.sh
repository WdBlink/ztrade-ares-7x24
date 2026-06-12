#!/usr/bin/env bash
# ares-cron-tick.sh — stateless 1-minute tick (PRD §4.2, §5.3)
# Defense in depth: if launchd is broken, cron still detects stuck runs.
# Also runs a daily SQLite snapshot at 03:00 (PRD §15.5).
set -euo pipefail
cd "$(dirname "$0")/.."

# Daily snapshot at 03:00 local time
HOUR=$(date +%H)
MINUTE=$(date +%M)
if [ "$HOUR" = "03" ] && [ "$MINUTE" = "00" ]; then
  ar724 snapshot || true
  # Prune snapshots older than 30 days
  find .ares/snapshots -name "state-*.db*" -mtime +30 -delete 2>/dev/null || true
fi

exec ar724 cron-tick
