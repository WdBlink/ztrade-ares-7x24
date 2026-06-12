"""Tmux session/window lifecycle.

PRD §4.3, §10.1, §10.2. Manages the `ar7x24-{run_id}` tmux session and its
windows (control, board, events, reaper, worker slots, evaluator, reviewer,
operator shell). Provides:

  - `create_session(run_id, num_worker_slots=4)` — make the session and all
    canonical windows.
  - `send_pane(tmux_target, command)` — send a command to a pane.
  - `kill_window(tmux_target)` — controller's unilateral force-kill (§10.2).
  - `pane_pid(tmux_target)` — read pane PID for liveness checks.
  - `list_sessions()`, `session_exists(name)` — for status checks.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# ── Window layout (PRD §4.3) ──────────────────────────────────────

WORKER_SLOTS = ("proposer", "builder", "validator", "reviewer")
WINDOW_LAYOUT = [
    ("control", 0, "controller"),
    ("board", 1, "board"),
    ("events", 2, "events"),
    ("reaper", 3, "reaper"),
    ("evaluator", 40, "evaluator"),
    ("shell", 90, "operator"),
]

# Worker slot windows are dynamic; we number them 10, 20, 30, 40 by default
# to leave headroom for future slot additions.
SLOT_WINDOW_INDICES = {"proposer": 10, "builder": 20, "validator": 30, "reviewer": 50}


@dataclass
class PaneInfo:
    target: str
    pid: int | None
    title: str | None


def tmux_exe() -> str:
    """Return the tmux binary path (or fail loudly if not installed)."""
    exe = shutil.which("tmux")
    if not exe:
        raise RuntimeError("tmux is not installed (required for 7×24 controller)")
    return exe


def session_name(run_id: str) -> str:
    """Return the canonical session name: ar7x24-{run_id}."""
    # Tmux session names are limited to alnum + dash + underscore; sanitize.
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", run_id)
    return f"ar7x24-{safe}"


def session_exists(name: str) -> bool:
    """Return True if a tmux session with this name exists."""
    out = subprocess.run(
        [tmux_exe(), "has-session", "-t", name],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def list_sessions() -> list[str]:
    """List all ar7x24-* tmux sessions."""
    out = subprocess.run(
        [tmux_exe(), "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return []
    return [
        line for line in out.stdout.splitlines()
        if line.startswith("ar7x24-")
    ]


def kill_session(name: str) -> None:
    """Tear down a tmux session. Idempotent."""
    if session_exists(name):
        subprocess.run(
            [tmux_exe(), "kill-session", "-t", name],
            capture_output=True, text=True,
        )


def new_window(session: str, name: str, command: str = "") -> str:
    """Create a new window; return its target (session:name).

    Idempotent: if a window with the same name already exists, it is killed
    and recreated (so `ar724 up` is safe to re-run).
    """
    tmux = tmux_exe()
    if name in list_windows(session):
        subprocess.run(
            [tmux, "kill-window", "-t", f"{session}:{name}"],
            capture_output=True, text=True,
        )
    cmd = [tmux, "new-window", "-d", "-t", session, "-n", name]
    if command:
        cmd += ["-P", "-F", "#{window_id}"]
        cmd += [command]  # tmux new-window treats remaining args as the command
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0 and "duplicate window" not in out.stderr:
        raise RuntimeError(
            f"tmux new-window failed for {session}:{name}: {out.stderr}"
        )
    return f"{session}:{name}"


def list_windows(session: str) -> list[str]:
    """List window names in a session."""
    if not session_exists(session):
        return []
    out = subprocess.run(
        [tmux_exe(), "list-windows", "-t", session, "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return []
    return [w for w in out.stdout.splitlines() if w]


def send_keys(tmux_target: str, *keys: str) -> None:
    """Send keys to a tmux pane (literal-then-Enter).

    Each key tuple element is one argument; use 'C-m' for Enter.
    """
    if not keys:
        return
    cmd = [tmux_exe(), "send-keys", "-t", tmux_target, *keys]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def send_command(tmux_target: str, command: str) -> None:
    """Convenience: send `command` and press Enter."""
    send_keys(tmux_target, command, "C-m")


def kill_window(tmux_target: str) -> None:
    """Unilaterally kill a tmux window (controller's force-kill authority).

    Per PRD §10.2, the conductor's `tmux kill-window` bypasses the worker's
    keepalive loop. The keepalive shell's on-exit trap fires EXIT, but the
    conductor does NOT consult `should-keepalive`.
    """
    if not tmux_target:
        return
    if ":" not in tmux_target:
        return
    subprocess.run(
        [tmux_exe(), "kill-window", "-t", tmux_target],
        capture_output=True, text=True,
    )


def pane_pid(tmux_target: str) -> int | None:
    """Return the PID of the pane's active process, or None if not found."""
    out = subprocess.run(
        [tmux_exe(), "list-panes", "-t", tmux_target, "-F", "#{pane_pid}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def is_pid_alive(pid: int | None) -> bool:
    """Return True if the process is still alive (POSIX kill -0)."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── High-level setup ──────────────────────────────────────────────

def create_session(run_id: str, working_dir: Path, *,
                   controller_cmd: str = "ar724 conductor",
                   board_cmd: str = "ar724 board",
                   events_cmd: str = "tail -f .ares/events.jsonl",
                   reaper_cmd: str = "ar724 cron-tick",
                   worker_role: str = "factor_combiner") -> str:
    """Create the full tmux session for a run.

    Idempotent: if the session already exists, returns its name without
    mutating it. Worker slots are created for the canonical 4-role layout.
    """
    name = session_name(run_id)
    if not session_exists(name):
        subprocess.run(
            [tmux_exe(), "new-session", "-d", "-s", name, "-n", "control",
             "-c", str(working_dir)],
            check=True, capture_output=True, text=True,
        )

    # Control window
    new_window(name, "control", controller_cmd)
    new_window(name, "board", board_cmd)
    new_window(name, "events", events_cmd)
    new_window(name, "reaper", reaper_cmd)
    new_window(name, "evaluator", "ar724 evaluator --watch")
    new_window(name, "shell", "bash")

    # Worker slot windows (4 fixed roles)
    for slot_name in WORKER_SLOTS:
        win_idx = SLOT_WINDOW_INDICES[slot_name]
        new_window(name, slot_name, "bash -i")  # keepalive wrap on first use

    return name


def attach_command(run_id: str) -> str:
    """Return the shell command a user runs to attach to the session."""
    return f"tmux attach -t {session_name(run_id)}"
