"""Controlled vocabulary for `event_type` field.

Per PRD §13.3, the event_type field is a closed set. Adding a new event
type requires registering it here AND in any dashboards that parse it.
"""

from __future__ import annotations

# Run lifecycle
RUN_LIFECYCLE = frozenset(
    {
        "run_created",
        "run_started",
        "run_paused",
        "run_resumed",
        "run_completed",
        "run_halted",
        "run_failed",
        "run_cancelled",
    }
)

# Iteration
ITERATION = frozenset(
    {
        "iter_queued",
        "iter_started",
        "iter_completed",
        "iter_blocked",
        "iter_discarded",
        "iter_vetoed",
    }
)

# Phase job
PHASE_JOB = frozenset(
    {
        "phase_queued",
        "phase_claimed",
        "phase_started",
        "phase_completed",
        "phase_failed",
        "phase_retry",
        "phase_orphaned",
    }
)

# Worker
WORKER = frozenset(
    {
        "worker_spawned",
        "worker_heartbeat",
        "worker_exited",
        "worker_force_killed",
    }
)

# Promotion
PROMOTION = frozenset(
    {
        "promotion_prepared",
        "promotion_applying",
        "promotion_committed",
        "promotion_failed",
        "promotion_reconciled",
    }
)

# Budget
BUDGET = frozenset(
    {
        "budget_warning",
        "budget_exceeded",
        "cost_anomaly",
        "cost_override",
    }
)

# Quality
QUALITY = frozenset(
    {
        "oscillation_detected",
        "consecutive_discard",
        "consecutive_blocked",
        "circuit_breaker_tripped",
    }
)

# Safety
SAFETY = frozenset(
    {
        "safety_policy_loaded",
        "safety_violation_blocked",
        "mcp_allowlist_changed",
    }
)

# Eval
EVAL = frozenset(
    {
        "eval_run_started",
        "eval_run_completed",
    }
)

# Trace / generic
TRACE = frozenset(
    {
        "run_state_transition",
        "trace",
        "metric",
    }
)

ALL_EVENT_TYPES: frozenset[str] = frozenset(
    RUN_LIFECYCLE
    | ITERATION
    | PHASE_JOB
    | WORKER
    | PROMOTION
    | BUDGET
    | QUALITY
    | SAFETY
    | EVAL
    | TRACE
)

SEVERITY_LEVELS = ("info", "warn", "error", "critical")


def is_valid_event_type(event_type: str) -> bool:
    """Return True if `event_type` is in the controlled vocabulary."""
    return event_type in ALL_EVENT_TYPES


def assert_valid_event_type(event_type: str) -> None:
    """Raise ValueError if `event_type` is not in the controlled vocabulary."""
    if not is_valid_event_type(event_type):
        raise ValueError(
            f"Unknown event_type: {event_type!r}. "
            f"Register in ar724.event_types.ALL_EVENT_TYPES first."
        )
