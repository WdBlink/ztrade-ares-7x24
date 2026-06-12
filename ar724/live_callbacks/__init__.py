"""Live callbacks (PRD §13.4).

Two built-in callbacks:
  - feishu_alert: fires on circuit_breaker_tripped, run_halted, budget_exceeded,
    oscillation_detected (if policy=halt)
  - tmux_status:  updates the 1.board window on every phase transition
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Severity threshold for Feishu alerts (configurable in loop_config.json)
FEISHU_MIN_SEVERITY_DEFAULT = "warn"

LIVE_CALLBACKS: dict[str, Callable[[dict[str, Any]], None]] = {}


def register(name: str, callback: Callable[[dict[str, Any]], None]) -> None:
    LIVE_CALLBACKS[name] = callback


def dispatch(event: dict[str, Any]) -> None:
    """Dispatch an event to all registered callbacks. Best-effort; failures
    are swallowed (callbacks must never break the main loop).
    """
    for name, cb in LIVE_CALLBACKS.items():
        try:
            cb(event)
        except Exception:
            pass


# ── feishu_alert ─────────────────────────────────────────────────

def feishu_alert(event: dict[str, Any]) -> None:
    """Send a Feishu webhook alert for high-severity events."""
    severity = event.get("severity", "info")
    if severity not in ("warn", "error", "critical"):
        return
    webhook = os.environ.get("ARES_FEISHU_WEBHOOK", "")
    if not webhook:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"ar724 {event.get('event_type', 'event')}",
                },
                "template": "red" if severity == "critical" else "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**{severity.upper()}** {event.get('event_type')}\n"
                            f"run_id: `{event.get('run_id', '?')}`\n"
                            f"```\n{json.dumps(event.get('payload', {}), indent=2)[:1500]}\n```"
                        ),
                    },
                },
            ],
        },
    }
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


# ── tmux_status ──────────────────────────────────────────────────

def tmux_status(event: dict[str, Any]) -> None:
    """Update the 1.board window with the latest event summary.

    Writes a tiny status file that the board tmux window can `cat` on a loop.
    """
    status_path = Path(os.environ.get("ARES_BOARD_STATUS", ".ares/board.json"))
    status_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "last_event_type": event.get("event_type"),
        "last_severity": event.get("severity"),
        "last_run_id": event.get("run_id"),
        "last_iteration_id": event.get("iteration_id"),
        "last_phase_job_id": event.get("phase_job_id"),
        "updated_at": event.get("created_at"),
    }
    try:
        with status_path.open("w") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
    except Exception:
        pass


# ── Default registrations ───────────────────────────────────────

register("feishu_alert", feishu_alert)
register("tmux_status", tmux_status)
