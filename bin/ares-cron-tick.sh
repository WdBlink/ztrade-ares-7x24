#!/usr/bin/env bash
# ares-cron-tick.sh — stateless 1-minute tick (PRD §4.2, §5.3)
# Defense in depth: if launchd is broken, cron still detects stuck runs.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ar724 cron-tick
