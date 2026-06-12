"""HeartbeatTimer — periodic liveness pings to SQLite/events.

Vibe-Trading port:
  Adapted from HKUDS/Vibe-Trading (MIT) agent/src/agent/progress.py:HeartbeatTimer.
  License: https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE

Wraps any long-running task call and writes a `worker_heartbeat` event every
`interval_s` seconds. If the worker process dies, heartbeats stop, and the
stale-reaper (§5.3) marks the phase job as orphaned.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, now_iso


class HeartbeatTimer:
    """Background thread that writes a heartbeat event every `interval_s`.

    Usage:
        timer = HeartbeatTimer(db, run_id, phase_job_id, interval_s=3.0)
        timer.start()
        try:
            long_running_call()
        finally:
            timer.stop()
    """

    def __init__(
        self,
        db: Database,
        run_id: str,
        phase_job_id: str,
        interval_s: float = 3.0,
        events_jsonl_path: Path | None = None,
        on_heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.phase_job_id = phase_job_id
        self.interval_s = interval_s
        self.events_jsonl_path = events_jsonl_path
        self.on_heartbeat = on_heartbeat
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._beat_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"heartbeat-{self.phase_job_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s * 2)
            self._thread = None

    @property
    def beat_count(self) -> int:
        return self._beat_count

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._beat()
            # wait_for returns True if the event is set during sleep (cancelled)
            if self._stop_event.wait(timeout=self.interval_s):
                return

    def _beat(self) -> None:
        self._beat_count += 1
        now = now_iso()
        try:
            self.db.execute(
                "UPDATE phase_jobs SET last_heartbeat_at = ? "
                "WHERE id = ? AND status = 'running'",
                (now, self.phase_job_id),
            )
            self._append_event(now)
            if self.on_heartbeat:
                self.on_heartbeat()
        except Exception:
            # Heartbeat must never crash the worker; swallow and try again.
            pass

    def _append_event(self, now: str) -> None:
        if not self.events_jsonl_path:
            return
        payload: dict[str, Any] = {
            "phase_job_id": self.phase_job_id,
            "beat_count": self._beat_count,
        }
        record = {
            "id": f"hb-{self.phase_job_id}-{self._beat_count}",
            "run_id": self.run_id,
            "phase_job_id": self.phase_job_id,
            "event_type": "worker_heartbeat",
            "severity": "info",
            "payload": payload,
            "created_at": now,
        }
        self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_jsonl_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
