"""Spawner — tmux new-window + env-file injection, keepalive wrap, force_kill.

PRD §10.3, §10.4. Re-implementation (not a copy) of ClawTeam patterns.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import tmux_manager


# ── env-file injection (PRD §10.4) ────────────────────────────────

def write_env_file(env_vars: Mapping[str, str]) -> Path:
    """Write env vars to a temp file. The pane sources it then deletes it.

    Re-implementation of ClawTeam's tmux_backend.py:146-153 pattern.
    """
    fd, name = tempfile.mkstemp(prefix="ares-env-", suffix=".sh", text=True)
    with os.fdopen(fd, "w") as f:
        for k, v in env_vars.items():
            if not k.isidentifier():
                # Skip non-shell-safe variable names; user must fix config.
                continue
            f.write(f"export {k}={shlex.quote(str(v))}\n")
        f.write(f"rm -f {shlex.quote(name)}\n")
    return Path(name)


# ── keepalive shell (PRD §10.1) ───────────────────────────────────

KEEPALIVE_TEMPLATE = """\
__ct_agent='{agent_name}'
__ct_cmd='{initial_cmd}'
__ct_resume='{resume_cmd}'
trap '__ct_status=$?; ares on-exit "$__ct_agent" $__ct_status' EXIT
while true; do
  eval "$__ct_cmd"
  __ct_status=$?
  if [ $__ct_status -eq 0 ] && ares lifecycle should-keepalive "$__ct_agent"; then
    __ct_cmd="$__ct_resume"
    sleep 2
    continue
  fi
  break
done
"""


def build_keepalive_script(
    agent_name: str, initial_cmd: str, resume_cmd: str = "claude --continue"
) -> str:
    """Return a bash script that wraps a command in the keepalive loop."""
    safe_initial = initial_cmd.replace("'", "'\\''")
    return KEEPALIVE_TEMPLATE.format(
        agent_name=agent_name,
        initial_cmd=safe_initial,
        resume_cmd=resume_cmd,
    )


# ── Spawner (high-level) ──────────────────────────────────────────

@dataclass
class SpawnResult:
    tmux_target: str
    env_file: Path
    keepalive_cmd: str
    pid: int | None = None


def spawn_worker(
    session: str,
    slot: str,
    *,
    initial_cmd: str,
    env: Mapping[str, str] | None = None,
    workdir: Path | None = None,
    resume_cmd: str = "claude --continue",
) -> SpawnResult:
    """Launch a worker in a tmux window with env-file injection and keepalive.

    `initial_cmd` is the first command; the keepalive shell will run
    `resume_cmd` if the initial exits 0 and the lifecycle gate allows.
    """
    env = dict(env or {})
    env_file = write_env_file(env)
    keepalive_script = build_keepalive_script(
        agent_name=slot, initial_cmd=initial_cmd, resume_cmd=resume_cmd,
    )
    target = f"{session}:{slot}"
    tmux_manager.send_command(target, "clear")

    # Inject the env file, then run the keepalive wrap.
    quoted_env = shlex.quote(str(env_file))
    quoted_cmd = shlex.quote(keepalive_script)
    full = f". {quoted_env} && bash -c {quoted_cmd}"
    if workdir:
        full = f"cd {shlex.quote(str(workdir))} && {full}"
    tmux_manager.send_command(target, full)

    return SpawnResult(
        tmux_target=target,
        env_file=env_file,
        keepalive_cmd=full,
        pid=tmux_manager.pane_pid(target),
    )


def force_kill_worker(session: str, slot: str, reason: str) -> None:
    """Hard-kill a keepalive window. Bypasses should-keepalive (PRD §10.2)."""
    target = f"{session}:{slot}"
    tmux_manager.kill_window(target)
