#!/usr/bin/env bash
# ares-status.sh — one-shot status query (PRD §22.4)
set -euo pipefail
exec ar724 status "$@"
