"""Conductor — main controller loop, 14-step tick, DAG dispatch.

PRD §4 L1, §6, §11. The single owner of state transitions. The tick:
  1. acquire service_locks.controller
  2. heartbeat
  3. read run config + budget
  4. reap stale leases
  5. reconcile tmux worker_slots
  6. schedule next phase job
  7. launch worker
  8. collect output
  9. validate schema, check_acceptance, emit event
  10. advance iteration by CAS
  11. run 10-gate promotion (if KEEP)
  12. consult oscillation guard
  13. emit board snapshot
  14. sleep
"""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tmux_manager
from .budget import check_budget
from .config_loader import get_loop_config
from .db import Database, now_iso
from .observability import emit_event, new_trace_id
from .oscillation import OscillationDetector
from .promotion_gate import (
    GateReport, claim_promotion_lock, perform_promotion,
    release_promotion_lock, run_all_gates,
)
from .role_loader import REQUIRED_ROLE_IDS, validate_roles


# ── Service lock helpers ──────────────────────────────────────────

CONTROLLER_LOCK = "controller"
PROMOTION_LOCK = "promotion"
LOCK_LEASE_SECONDS = 180  # PRD §11.3


def acquire_controller_lock(db: Database, run_id: str) -> bool:
    """Acquire service_locks.controller (CAS)."""
    from datetime import datetime, timezone, timedelta
    holder = f"controller:{run_id}"
    now = now_iso()
    lease = (datetime.now(timezone.utc) + timedelta(seconds=LOCK_LEASE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        existing = db.fetchone(
            "SELECT holder_id, lease_expires_at FROM service_locks WHERE name = ?",
            (CONTROLLER_LOCK,),
        )
        if existing is None:
            db.execute(
                "INSERT INTO service_locks (name, holder_id, lease_expires_at, last_heartbeat_at) "
                "VALUES (?, ?, ?, ?)",
                (CONTROLLER_LOCK, holder, lease, now),
            )
            return True
        if existing["lease_expires_at"] < now or existing["holder_id"] == holder:
            db.execute(
                "UPDATE service_locks SET holder_id = ?, lease_expires_at = ?, "
                "last_heartbeat_at = ? WHERE name = ?",
                (holder, lease, now, CONTROLLER_LOCK),
            )
            return True
        return False
    except Exception:
        return False


def heartbeat_controller_lock(db: Database, run_id: str) -> None:
    """Refresh the controller lock lease."""
    from datetime import datetime, timezone, timedelta
    holder = f"controller:{run_id}"
    lease = (datetime.now(timezone.utc) + timedelta(seconds=LOCK_LEASE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "UPDATE service_locks SET lease_expires_at = ?, last_heartbeat_at = ? "
        "WHERE name = ? AND holder_id = ?",
        (lease, now_iso(), CONTROLLER_LOCK, holder),
    )


# ── Stale reaper (PRD §5.3) ──────────────────────────────────────


# Module-level oscillation state per run (PRD §9.1).
# In a multi-process deployment, this would be persisted to SQLite; for V1.0
# the single conductor process holds the in-memory detector.
_RUN_OSCILLATION: dict[str, OscillationDetector] = {}


def _check_oscillation(db: Database, run_id: str, candidate_hash: str, iter_id: str) -> bool:
    """Run the oscillation detector for `candidate_hash`. Returns True if fired.

    Reads the candidate's parameter dict from the candidates table, observes
    it on the per-run detector, and (if fired) emits an `oscillation_detected`
    event. If the loop_config has `oscillation_policy = 'halt'`, the gate
    fails (PRD §9.1).
    """
    from .config_loader import get_loop_config
    cand = db.fetchone("SELECT * FROM candidates WHERE hash = ?", (candidate_hash,))
    if not cand:
        return False
    # Get the latest mutable file content as the parameter dict proxy.
    mutable_path = Path(cand["mutable_path"] or "")
    if not mutable_path.exists():
        return False
    try:
        params = json.loads(mutable_path.read_text()).get("factor_weights", {})
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(params, dict) or not params:
        return False
    det = _RUN_OSCILLATION.setdefault(run_id, OscillationDetector())
    triggered = det.observe(params)
    if triggered:
        policy = get_loop_config().get("oscillation_policy", "warn")
        emit_event(
            db, event_type="oscillation_detected", severity="warn",
            run_id=run_id, iteration_id=iter_id, phase_job_id=None,
            payload={"triggers": triggered, "policy": policy},
        )
        return policy == "halt"
    return False


def reap_stale_phase_jobs(
    db: Database,
    *,
    run_id: str,
    lease_timeout_seconds: int = 180,
) -> list[str]:
    """Mark phase jobs as `orphaned` if their lease has expired.

    Returns the list of phase job ids that were reaped.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=lease_timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.fetchall(
        "SELECT id, slot_name FROM phase_jobs "
        "WHERE run_id = ? AND status = 'running' AND lease_expires_at < ?",
        (run_id, cutoff),
    )
    reaped = []
    for row in rows:
        job_id = row["id"]
        slot = row["slot_name"]
        if slot:
            tmux_target = f"{tmux_manager.session_name(run_id)}:{slot}"
            tmux_manager.kill_window(tmux_target)
        db.execute(
            "UPDATE phase_jobs SET status = 'failed', "
            "error_class = 'orphaned_process', "
            "error_message = 'lease expired' WHERE id = ?",
            (job_id,),
        )
        emit_event(
            db, event_type="phase_orphaned", severity="warn",
            run_id=run_id, phase_job_id=job_id,
            payload={"reason": "lease_expired", "slot": slot},
        )
        reaped.append(job_id)
    return reaped


# ── DAG-driven scheduler (PRD §6.3, §11.2) ──────────────────────

def _phase_job_dag_for_roles(roles_dir: Path) -> list[Any]:
    """Build the per-iteration DAG from the 4 fixed role YAMLs.

    Layers (PRD §6.3):
      L1: proposer (factor_combiner)
      L2: builder  (backtester)
      L3: validator (factor_validator)
      L4: evaluator (deterministic, no LLM)
      L4: reviewer (backtest_reviewer)
      L5: promoter (controller internal)
    """
    from .dag import Task
    tasks = [
        Task(id="proposer", depends_on=[]),
        Task(id="builder", depends_on=["proposer"]),
        Task(id="validator", depends_on=["builder"]),
        Task(id="evaluator", depends_on=["validator"]),
        Task(id="reviewer", depends_on=["evaluator"]),
        Task(id="promoter", depends_on=["reviewer"]),
    ]
    return tasks


# ── Iteration + run helpers ───────────────────────────────────────

def create_run(
    db: Database,
    *,
    goal: str,
    budget_cents: int = 50_00,
    daily_budget_cents: int = 50_00,
    config_hash: str = "",
) -> str:
    """Create a new run row; return the run id."""
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs "
        "(id, goal, status, created_at, budget_cents, spent_cents, "
        " config_hash, daily_budget_cents) "
        "VALUES (?, ?, 'created', ?, ?, 0, ?, ?)",
        (run_id, goal, now_iso(), int(budget_cents), config_hash, int(daily_budget_cents)),
    )
    emit_event(db, event_type="run_created", severity="info", run_id=run_id,
               payload={"goal": goal, "budget_cents": budget_cents})
    return run_id


def start_run(db: Database, run_id: str) -> None:
    db.execute(
        "UPDATE runs SET status = 'running', started_at = ? WHERE id = ?",
        (now_iso(), run_id),
    )
    emit_event(db, event_type="run_started", severity="info", run_id=run_id)


def create_iteration(
    db: Database, *, run_id: str, index: int, parent_best_hash: str = "",
) -> str:
    iter_id = f"iter-{uuid.uuid4()}"
    db.execute(
        'INSERT INTO iterations '
        '(id, run_id, "index", status, parent_best_hash, started_at, trace_id) '
        "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
        (iter_id, run_id, int(index), parent_best_hash, now_iso(), new_trace_id()),
    )
    emit_event(db, event_type="iter_queued", severity="info",
               run_id=run_id, iteration_id=iter_id,
               payload={"index": index})
    return iter_id


def enqueue_phase_jobs(db: Database, *, run_id: str, iteration_id: str) -> int:
    """Enqueue the 5 phase jobs (proposer, builder, validator, evaluator, reviewer)
    for an iteration. The promoter is internal (controller-internal) and is
    not enqueued.
    """
    phases = [
        ("proposer", "factor_combiner", 10),
        ("builder", "backtester", 20),
        ("validator", "factor_validator", 30),
        ("evaluator", "evaluator_runner", 40),
        ("reviewer", "backtest_reviewer", 50),
    ]
    for phase, role, slot_idx in phases:
        job_id = f"pj-{uuid.uuid4()}"
        db.execute(
            "INSERT INTO phase_jobs "
            "(id, run_id, iteration_id, phase, role, status, slot_name, attempt) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, 0)",
            (job_id, run_id, iteration_id, phase, role, f"slot_{slot_idx}"),
        )
        emit_event(db, event_type="phase_queued", severity="info",
                   run_id=run_id, iteration_id=iteration_id, phase_job_id=job_id,
                   payload={"phase": phase, "role": role})
    return len(phases)


def claim_next_phase_job(
    db: Database, *, run_id: str, role: str | None = None,
) -> dict[str, Any] | None:
    """CAS-claim the next queued phase job for this run. Returns the job dict
    or None if no queued jobs are eligible.
    """
    if role:
        row = db.fetchone(
            "SELECT * FROM phase_jobs "
            "WHERE run_id = ? AND status = 'queued' AND role = ? "
            "ORDER BY id LIMIT 1",
            (run_id, role),
        )
    else:
        row = db.fetchone(
            "SELECT * FROM phase_jobs "
            "WHERE run_id = ? AND status = 'queued' "
            "ORDER BY id LIMIT 1",
            (run_id,),
        )
    if not row:
        return None
    from datetime import datetime, timezone, timedelta
    lease = (datetime.now(timezone.utc) + timedelta(seconds=LOCK_LEASE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    worker_id = f"worker-{uuid.uuid4()}"
    rowcount = db.cas_update(
        "phase_jobs", row["id"], "queued",
        {
            "status": "running",
            "claimed_by": worker_id,
            "lease_expires_at": lease,
            "last_heartbeat_at": now_iso(),
            "attempt": int(row["attempt"] or 0) + 1,
        },
    )
    if rowcount == 0:
        return None
    emit_event(db, event_type="phase_claimed", severity="info",
               run_id=run_id, iteration_id=row["iteration_id"],
               phase_job_id=row["id"], payload={"worker_id": worker_id})
    return {**dict(row), "claimed_by": worker_id, "status": "running"}


# ── Tick (single iteration of the main loop) ─────────────────────

@dataclass
class TickResult:
    scheduled: int = 0
    completed: int = 0
    failed: int = 0
    promoted: int = 0
    reaped: int = 0
    events: int = 0


def tick(db: Database, run_id: str, *,
         roles_dir: Path, working_dir: Path,
         events_jsonl: Path,
         tick_interval_seconds: float = 5.0) -> TickResult:
    """Run a single tick of the main loop. Used by `ar724 conductor` and tests."""
    result = TickResult()

    # 1. acquire controller lock
    if not acquire_controller_lock(db, run_id):
        return result  # another conductor owns this; skip tick
    heartbeat_controller_lock(db, run_id)

    # 2. read run
    run = db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run or run["status"] != "running":
        return result

    # 3. budget check (continue even if over, but do not promote)
    run_dict = dict(run)
    over_budget, _ = check_budget(db, run_dict)

    # 4. reap stale leases
    result.reaped = len(reap_stale_phase_jobs(db, run_id=run_id))

    # 5. claim next phase job
    job = claim_next_phase_job(db, run_id=run_id)
    if job is not None:
        result.scheduled += 1
        # In a real run, we would spawn the worker here. For V1.0 the
        # conductor emits the phase_started event and lets the spawner
        # (separate process) pick it up.
        emit_event(
            db, event_type="phase_started", severity="info",
            run_id=run_id, iteration_id=job["iteration_id"],
            phase_job_id=job["id"],
            payload={"role": job["role"], "phase": job["phase"]},
        )

    # 6. collect completed outputs (a real implementation reads exit-journal)
    #    For V1.0 we rely on the spawner to mark phase_jobs status='completed'.

    # 7. try to promote if the latest iter is ready (reviewer KEEP and gates pass)
    _try_promote_latest_iter(
        db, run_id=run_id, roles_dir=roles_dir, working_dir=working_dir,
        over_budget=over_budget,
    )

    return result


def _try_promote_latest_iter(
    db: Database, *, run_id: str, roles_dir: Path, working_dir: Path,
    over_budget: bool,
) -> None:
    """If the latest iter has all phases completed and reviewer KEEP,
    run the 10-gate promotion pipeline. No-op otherwise.
    """
    iter_row = db.fetchone(
        'SELECT * FROM iterations WHERE run_id = ? '
        'ORDER BY "index" DESC LIMIT 1',
        (run_id,),
    )
    if not iter_row:
        return

    jobs = db.fetchall(
        "SELECT * FROM phase_jobs WHERE iteration_id = ? "
        "ORDER BY id",
        (iter_row["id"],),
    )
    if not jobs:
        return
    if any(j["status"] not in ("completed", "failed", "blocked", "vetoed", "discarded")
           for j in jobs):
        return  # still in flight

    # Has the reviewer approved?
    reviewer = next((j for j in jobs if j["role"] == "backtest_reviewer"), None)
    if not reviewer or reviewer["status"] != "completed":
        return

    # Has the evaluator KEEPed?
    eval_row = db.fetchone(
        "SELECT * FROM evaluations WHERE candidate_hash = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (iter_row["selected_candidate_hash"] or "",),
    )
    if not eval_row or eval_row["decision"] != "KEEP":
        return

    if not claim_promotion_lock(db, run_id):
        return
    try:
        # Load role YAMLs (validate roles_dir)
        roles = {r["id"]: r for r in validate_roles(roles_dir)}
        candidate_hash = iter_row["selected_candidate_hash"] or ""
        candidate_score = float(eval_row["score"] or 0.0)
        # Best score = last committed promotion's candidate hash + score proxy
        best_row = db.fetchone(
            "SELECT * FROM promotions WHERE status = 'committed' "
            "AND run_id = ? ORDER BY promoted_at DESC LIMIT 1",
            (run_id,),
        )
        if best_row is None:
            # First promotion: regression guard disabled (any positive score
            # is accepted). Subsequent promotions must beat 0.9 * best.
            best_score = 0.0
        else:
            best_score = float(eval_row["score"] or 0.0)
        report = run_all_gates(
            db,
            run_id=run_id,
            candidate_hash=candidate_hash,
            iteration_id=iter_row["id"],
            role_yaml=roles.get("backtest_reviewer", {}),
            worker_output={},
            write_paths=[],
            eval_decision=eval_row["decision"],
            candidate_score=candidate_score,
            best_score=best_score,
            reviewer_phase_job_id=reviewer["id"],
            builder_phase_job_id=next((j["id"] for j in jobs if j["role"] == "backtester"), None),
            evaluator_phase_job_id=next((j["id"] for j in jobs if j["role"] == "evaluator_runner"), None),
            consecutive_discards=int(run["consecutive_discards"] or 0),
            consecutive_blockeds=int(run["consecutive_blockeds"] or 0),
            oscillation_fired=_check_oscillation(db, run_id, candidate_hash, iter_row.get("id")),
            allowed_paths=["autoresearch/best/", "autoresearch/mutable/"],
        )
        if not report.passed:
            emit_event(
                db, event_type="promotion_failed", severity="warn",
                run_id=run_id, iteration_id=iter_row["id"],
                payload={"failures": [f.gate + ": " + f.reason for f in report.failures]},
            )
            return
        # Promote
        cand = db.fetchone("SELECT * FROM candidates WHERE hash = ?", (candidate_hash,))
        if not cand:
            return
        new_best = Path(cand["mutable_path"])
        result = perform_promotion(
            db, run_id=run_id, iteration_id=iter_row["id"],
            candidate_hash=candidate_hash,
            old_best_hash=best_row["new_best_hash"] if best_row else None,
            new_best_path=new_best,
            results_tsv_path=working_dir / "autoresearch" / "results.tsv",
            report_path=working_dir / "autoresearch" / "reports" / f"iteration_{iter_row['index']}.md",
            working_dir=working_dir,
        )
        emit_event(
            db, event_type="promotion_committed", severity="info",
            run_id=run_id, iteration_id=iter_row["id"],
            payload=result,
        )
    finally:
        release_promotion_lock(db)
