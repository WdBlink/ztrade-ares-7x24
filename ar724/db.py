"""SQLite schema, migrations, atomic_write, replace_with_retry.

SQLite is the source of truth for run/iter/job state (PRD §5.1, §5.2).
File-based state.json and events.jsonl are SIDE-CHANNELS for live callbacks
only (PRD §5.4); they are never consulted for state transitions.

Vibe-Trading port:
  - atomic_write / replace_with_retry adapted from
    HKUDS/Vibe-Trading (MIT) agent/src/swarm/store.py:80-110
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# ── Vibe-Trading port: replace_with_retry ─────────────────────────
# Adapted from HKUDS/Vibe-Trading (MIT License)
# Original: agent/src/swarm/store.py:90-100+
# Modifications: docstring; re-raise non-transient errors immediately.
# License: https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE

_TRANSIENT_WINERRORS = (5, 32)  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF = (0.025, 0.05, 0.1, 0.2, 0.4)  # seconds


def replace_with_retry(tmp: Path, target: Path) -> None:
    """os.replace retried on the Windows concurrent-access race.

    POSIX os.replace is atomic and never raises these; on POSIX the loop
    runs exactly once. Non-transient errors re-raise immediately.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS:
                if attempt < _REPLACE_ATTEMPTS - 1:
                    time.sleep(_REPLACE_BACKOFF[attempt])
                    continue
            raise


def atomic_write(target: Path, data: str | bytes) -> None:
    """Write `data` to `target` via tmp + replace, with Windows race retry.

    On POSIX this is a single os.replace (atomic). Use this for any non-SQLite
    artifact writes (state.json snapshot, config snapshots, JSON side-files).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        replace_with_retry(Path(name), target)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


# ── SQLite schema (PRD §5.2) ──────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,
  goal            TEXT,
  status          TEXT CHECK(status IN ('created','running','paused','waiting_approval','completed','failed','cancelled','scheduled_retry')),
  created_at      TEXT,
  started_at      TEXT,
  completed_at    TEXT,
  budget_cents    INTEGER,
  spent_cents     INTEGER,
  stop_reason     TEXT,
  config_hash     TEXT,
  daily_budget_cents INTEGER DEFAULT 5000,
  consecutive_discards INTEGER DEFAULT 0,
  consecutive_blockeds INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS iterations (
  id                      TEXT PRIMARY KEY,
  run_id                  TEXT REFERENCES runs(id),
  "index"                 INTEGER,
  status                  TEXT,
  parent_best_hash        TEXT,
  selected_candidate_hash TEXT,
  started_at              TEXT,
  completed_at            TEXT,
  summary                 TEXT,
  trace_id                TEXT
);

CREATE TABLE IF NOT EXISTS phase_jobs (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT REFERENCES runs(id),
  iteration_id        TEXT REFERENCES iterations(id),
  phase               TEXT,
  role                TEXT,
  status              TEXT CHECK(status IN ('queued','running','completed','failed','blocked','vetoed','discarded')),
  slot_name           TEXT,
  claimed_by          TEXT,
  attempt             INTEGER DEFAULT 0,
  lease_expires_at    TEXT,
  last_heartbeat_at   TEXT,
  input_hash          TEXT,
  output_hash         TEXT,
  candidate_hash      TEXT,
  error_class         TEXT,
  error_message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_phase_jobs_status ON phase_jobs(status);
CREATE INDEX IF NOT EXISTS idx_phase_jobs_run ON phase_jobs(run_id);

CREATE TABLE IF NOT EXISTS worker_slots (
  name                TEXT PRIMARY KEY,
  role                TEXT,
  tmux_target         TEXT,
  pane_pid            INTEGER,
  status              TEXT,
  current_phase_job_id TEXT,
  last_seen_at        TEXT,
  last_exit_code      INTEGER
);

CREATE TABLE IF NOT EXISTS candidates (
  hash                TEXT PRIMARY KEY,
  run_id              TEXT,
  iteration_id        TEXT,
  worktree_path       TEXT,
  mutable_path        TEXT,
  proposal_json_path  TEXT,
  patch_manifest_path TEXT,
  created_by_phase_job_id TEXT,
  schema_status       TEXT,
  stale_status        TEXT,
  created_at          TEXT
);

CREATE TABLE IF NOT EXISTS evaluations (
  id                  TEXT PRIMARY KEY,
  candidate_hash      TEXT,
  evaluator_run_dir   TEXT,
  run_status_path     TEXT,
  metrics_json_path   TEXT,
  decision            TEXT,
  score               REAL,
  error_message       TEXT,
  created_at          TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
  id                          TEXT PRIMARY KEY,
  candidate_hash              TEXT,
  reviewer_phase_job_id       TEXT,
  verdict                     TEXT,
  risk_flags_json             TEXT,
  rationale_path              TEXT,
  created_at                  TEXT
);

CREATE TABLE IF NOT EXISTS promotions (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT,
  iteration_id        TEXT,
  candidate_hash      TEXT,
  old_best_hash       TEXT,
  new_best_hash       TEXT,
  idempotency_key     TEXT UNIQUE,
  status              TEXT CHECK(status IN ('prepared','applying','committed','failed')),
  staged_manifest_path TEXT,
  git_commit          TEXT,
  promoted_at         TEXT,
  promoted_by         TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
  id                      TEXT PRIMARY KEY,
  run_id                  TEXT,
  requested_by            TEXT,
  reason                  TEXT,
  risk_tier               TEXT,
  status                  TEXT,
  proposed_change_json    TEXT,
  created_at              TEXT,
  expires_at              TEXT,
  decided_at              TEXT,
  decided_by              TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id              TEXT PRIMARY KEY,
  run_id          TEXT,
  iteration_id    TEXT,
  phase_job_id    TEXT,
  event_type      TEXT,
  severity        TEXT,
  payload_json    TEXT,
  created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS cost_events (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT,
  phase_job_id        TEXT,
  provider            TEXT,
  model               TEXT,
  input_tokens        INTEGER,
  output_tokens       INTEGER,
  cache_read_tokens   INTEGER,
  cache_write_tokens  INTEGER,
  cost_cents          INTEGER,
  created_at          TEXT
);

CREATE TABLE IF NOT EXISTS service_locks (
  name                TEXT PRIMARY KEY,
  holder_id            TEXT,
  lease_expires_at     TEXT,
  last_heartbeat_at    TEXT
);

CREATE TABLE IF NOT EXISTS eval_results (
  id          TEXT PRIMARY KEY,
  run_id      TEXT,
  name        TEXT,
  result      TEXT CHECK(result IN ('pass','fail','inconclusive')),
  metrics     TEXT,
  created_at  TEXT
);
"""


class Database:
    """Thread-safe SQLite wrapper with WAL mode.

    Used as the source of truth for all controller state. All writes go through
    this class so we can ensure WAL mode, foreign keys, and connection pooling
    are correctly configured.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a per-thread SQLite connection.

        Connections are cached per-thread (sqlite3 is not thread-safe to share
        across threads in older Python builds). Caller commits; this context
        does NOT auto-commit.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path),
                isolation_level=None,  # autocommit; we manage txns explicitly
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        try:
            yield conn
        except Exception:
            # Roll back on exception; connections are persistent so we don't close.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    # ── High-level helpers ──────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self.connection() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        with self._lock, self.connection() as conn:
            return conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def cas_update(self, table: str, id_value: str, expected_status: str,
                   updates: dict[str, Any]) -> int:
        """Compare-and-swap update on a single row identified by `id_value`.

        Returns the number of rows updated (0 = lost the race, 1 = won).
        Used to atomically claim queued phase jobs (PRD §6.4).
        """
        if not updates:
            raise ValueError("cas_update requires at least one update field")
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        sql = (
            f"UPDATE {table} "
            f"SET {set_clause} "
            f"WHERE id = :id AND status = :expected_status"
        )
        params = {**updates, "id": id_value, "expected_status": expected_status}
        with self._lock, self.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def now_iso() -> str:
    """Return current time as ISO-8601 UTC, second precision (PRD §5.2 style)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
