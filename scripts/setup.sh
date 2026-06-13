#!/usr/bin/env bash
# Skill dependency installer for ztrade-ares-7x24.
#
# Per PRD §16.2 the Skill and the Controller live in the same repo. This
# setup.sh does ONE thing: install the `ar724` binary from THIS repo's source
# (or an explicit override) so the Skill prompt can invoke it via Bash.
#
# Install source precedence:
#   1. $AR724_INSTALL_SOURCE env var (e.g. "git+https://github.com/me/fork.git@main")
#   2. This repo's own source (./) — the controller is shipped in the same repo
#   3. ./vendor/ar724/  (if user has the controller source alongside)
#   4. default: git+https://github.com/WdBlink/ztrade-ares-7x24.git@main
#
# Safe to re-run (idempotent). No sudo required.
set -euo pipefail

# Resolve repo root (the dir containing this script's parent is the project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "ztrade-ares-7x24: checking dependencies..."
echo ""

errors=0

# --- ar724 CLI ---
if command -v ar724 &>/dev/null; then
    echo "  ar724: $(command -v ar724) — already installed"
    ar724 --version 2>/dev/null || echo "  ar724: installed (version unknown)"
else
    echo "  ar724: NOT FOUND — installing"

    if [ -n "${AR724_INSTALL_SOURCE:-}" ]; then
        source_url="$AR724_INSTALL_SOURCE"
    elif [ -f "$REPO_ROOT/pyproject.toml" ] && [ -d "$REPO_ROOT/ar724" ]; then
        # Install from THIS repo (per PRD §16.2 the controller lives here)
        source_url="$REPO_ROOT"
    elif [ -d "./vendor/ar724" ] && [ -f "./vendor/ar724/pyproject.toml" ]; then
        source_url="./vendor/ar724"
    else
        source_url="git+https://github.com/WdBlink/ztrade-ares-7x24.git@main"
    fi

    echo "  install source: $source_url"

    if ! command -v python3 &>/dev/null; then
        echo "  ERROR: python3 not found. Install Python 3.11+ and re-run."
        errors=$((errors + 1))
    elif ! command -v pip &>/dev/null && ! python3 -m pip --version &>/dev/null; then
        echo "  ERROR: pip not found. Install pip and re-run."
        errors=$((errors + 1))
    else
        python3 -m pip install --user "$source_url" || errors=$((errors + 1))
    fi
fi

# --- tmux (required by the controller runtime) ---
if command -v tmux &>/dev/null; then
    echo "  tmux: $(command -v tmux)"
else
    echo "  ERROR: tmux not found. The ar724 controller uses tmux for worker sessions."
    echo "  Install: brew install tmux   (macOS)   |   apt install tmux   (Linux)"
    errors=$((errors + 1))
fi

# --- Optional: sqlite3 (for runbook 08 diagnosis) ---
if command -v sqlite3 &>/dev/null; then
    echo "  sqlite3: $(command -v sqlite3)"
else
    echo "  WARN: sqlite3 not found. Runbook 08 (restore SQLite snapshot) needs it."
    echo "  Pre-installed on most macOS/Linux; not blocking for skill operation."
fi

echo ""

if [ $errors -gt 0 ]; then
    echo "BLOCKED: $errors dependency issue(s). Fix above and re-run."
    exit 1
fi

echo "All dependencies ready. Try: ar724 --version"
echo "Then invoke the skill: /ztrade-ares-7x24 status"
exit 0
