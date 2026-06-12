#!/usr/bin/env bash
# ares-halt.sh — write .circuit-breaker (PRD §15.4 runbook 01)
set -euo pipefail
REASON="${1:-manual halt}"
ar724 halt "$REASON" --force
echo "halted: $REASON"
