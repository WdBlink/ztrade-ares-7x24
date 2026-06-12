"""Observability — traces, metrics, logs, evals.

PRD §13.1 (the four pillars). V1.0 implements all four.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .db import Database, now_iso
from .event_types import assert_valid_event_type


def new_trace_id() -> str:
    """Return a new trace_id (UUID4)."""
    return str(uuid.uuid4())


def emit_event(
    db: Database,
    *,
    event_type: str,
    severity: str = "info",
    run_id: str | None = None,
    iteration_id: str | None = None,
    phase_job_id: str | None = None,
    payload: dict[str, Any] | None = None,
    events_jsonl_path: Path | None = None,
) -> str:
    """Append an event to SQLite `events` table AND to events.jsonl (if given).

    Returns the event id. The two writes are not transactional (events.jsonl
    is a side-channel; SQLite is the truth).
    """
    assert_valid_event_type(event_type)
    if severity not in ("info", "warn", "error", "critical"):
        raise ValueError(f"Invalid severity: {severity!r}")
    event_id = f"evt-{uuid.uuid4()}"
    created_at = now_iso()
    payload_json = json.dumps(payload or {})

    db.execute(
        "INSERT INTO events "
        "(id, run_id, iteration_id, phase_job_id, event_type, severity, "
        " payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, run_id, iteration_id, phase_job_id, event_type,
         severity, payload_json, created_at),
    )

    if events_jsonl_path is not None:
        events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": event_id,
            "run_id": run_id,
            "iteration_id": iteration_id,
            "phase_job_id": phase_job_id,
            "event_type": event_type,
            "severity": severity,
            "payload": payload or {},
            "created_at": created_at,
        }
        with events_jsonl_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    return event_id


# ── Metrics (PRD §13.1) ───────────────────────────────────────────

_METRICS_SQL: dict[str, str] = {
    "runs.total_active": (
        "SELECT COUNT(*) AS v FROM runs WHERE status = 'running'"
    ),
    "iterations.completed.total": (
        "SELECT COUNT(*) AS v FROM iterations WHERE status = 'completed'"
    ),
    "iterations.completed.last_24h": (
        "SELECT COUNT(*) AS v FROM iterations "
        "WHERE status = 'completed' "
        "AND completed_at >= datetime('now', '-1 day')"
    ),
    "promotion.committed.total": (
        "SELECT COUNT(*) AS v FROM promotions WHERE status = 'committed'"
    ),
    "discards.consecutive.current": (
        "SELECT COALESCE(MAX(consecutive_discards), 0) AS v FROM runs"
    ),
    "blockeds.consecutive.current": (
        "SELECT COALESCE(MAX(consecutive_blockeds), 0) AS v FROM runs"
    ),
    "cost.cents.today": (
        "SELECT COALESCE(SUM(cost_cents), 0) AS v FROM cost_events "
        "WHERE created_at >= datetime('now', 'start of day')"
    ),
    "oscillation.detections.total": (
        "SELECT COUNT(*) AS v FROM events WHERE event_type = 'oscillation_detected'"
    ),
    "workers.active.current": (
        "SELECT COUNT(*) AS v FROM worker_slots WHERE status = 'running'"
    ),
    "evaluator.failures.consecutive": (
        "SELECT COALESCE(MAX(consecutive_blockeds), 0) AS v FROM runs"
    ),
}


def compute_metrics(db: Database) -> dict[str, int]:
    """Compute the §13.1 metric counters from SQLite. Returns {name: value}."""
    out: dict[str, int] = {}
    for name, sql in _METRICS_SQL.items():
        row = db.fetchone(sql)
        out[name] = int(row["v"]) if row else 0
    # cost.cents.per_iter is a moving average; we approximate with last 10
    avg_row = db.fetchone(
        "SELECT COALESCE(AVG(cost_cents), 0) AS v "
        "FROM (SELECT cost_cents FROM cost_events "
        "ORDER BY created_at DESC LIMIT 10)"
    )
    out["cost.cents.per_iter"] = int(avg_row["v"]) if avg_row else 0
    return out


# ── Evals (PRD §13.1 fourth pillar) ───────────────────────────────

def record_eval_result(
    db: Database,
    *,
    name: str,
    result: str,
    run_id: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> str:
    """Insert into eval_results. result must be 'pass'|'fail'|'inconclusive'."""
    if result not in ("pass", "fail", "inconclusive"):
        raise ValueError(f"Invalid eval result: {result!r}")
    eval_id = f"eval-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO eval_results (id, run_id, name, result, metrics, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (eval_id, run_id, name, result, json.dumps(metrics or {}), now_iso()),
    )
    return eval_id


def list_eval_results(db: Database) -> list[sqlite3.Row]:
    """Return all eval_results rows, newest first."""
    return db.fetchall(
        "SELECT * FROM eval_results ORDER BY created_at DESC"
    )


def tail_events(
    db: Database, *, since_seconds: int | None = None,
    severity: str | None = None, limit: int = 100,
) -> list[sqlite3.Row]:
    """Return recent events, optionally filtered by time and severity."""
    clauses = []
    params: list[Any] = []
    if since_seconds is not None:
        clauses.append(
            "created_at >= datetime('now', ?)"
        )
        params.append(f"-{int(since_seconds)} seconds")
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    return db.fetchall(sql, tuple(params))
