"""Cost control and model routing.

PRD §14. Pre-call token estimation, budget gates, anomaly response.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config_loader import get_model_profiles, get_role_routing
from .db import Database, now_iso


# ── Pre-call estimator (PRD §14.2) ────────────────────────────────

@dataclass
class PreCallEstimate:
    profile: str
    input_tokens: int
    expected_output_tokens: int
    cost_cents: float
    allowed: bool
    reason: str = ""


def estimate_call(
    role: str,
    input_text: str,
    expected_output_tokens: int = 4096,
) -> PreCallEstimate:
    """Estimate cost for a worker call and decide whether to allow it.

    Returns a PreCallEstimate. `allowed=False` means the call should be
    rejected by the caller (over budget, missing profile, etc.).
    """
    routing = get_role_routing()
    role_cfg = routing.get(role, {})
    profile_name = role_cfg.get("default_profile")
    if not profile_name:
        # Null profile = no LLM (deterministic role)
        return PreCallEstimate(
            profile="null",
            input_tokens=0,
            expected_output_tokens=0,
            cost_cents=0.0,
            allowed=True,
            reason="deterministic role; no LLM cost",
        )

    profiles = get_model_profiles()
    profile = profiles.get(profile_name)
    if not profile:
        return PreCallEstimate(
            profile=profile_name,
            input_tokens=0,
            expected_output_tokens=0,
            cost_cents=0.0,
            allowed=False,
            reason=f"model profile not found: {profile_name}",
        )

    # Rough token estimate: 1 token ≈ 4 chars for English
    input_tokens = max(1, len(input_text) // 4)
    max_input = int(profile.get("max_tokens_per_call", 200_000))
    if input_tokens > max_input:
        return PreCallEstimate(
            profile=profile_name,
            input_tokens=input_tokens,
            expected_output_tokens=expected_output_tokens,
            cost_cents=0.0,
            allowed=False,
            reason=f"input {input_tokens} > max {max_input}",
        )

    cost = (
        (input_tokens / 1000.0) * float(profile.get("pricing_per_1k_input", 0))
        + (expected_output_tokens / 1000.0)
        * float(profile.get("pricing_per_1k_output", 0))
    )
    cost_cents = cost * 100.0

    return PreCallEstimate(
        profile=profile_name,
        input_tokens=input_tokens,
        expected_output_tokens=expected_output_tokens,
        cost_cents=cost_cents,
        allowed=True,
    )


# ── Budget gates (PRD §14.3) ──────────────────────────────────────

def check_budget(db: Database, run: dict[str, Any]) -> tuple[bool, str]:
    """Return (allowed, reason). `reason` is empty if allowed."""
    budget_cents = int(run.get("budget_cents", 0))
    daily_budget_cents = int(run.get("daily_budget_cents", budget_cents))
    run_id = run["id"]
    rows = db.fetchall(
        "SELECT cost_cents, created_at FROM cost_events WHERE run_id = ?",
        (run_id,),
    )
    spent = sum(int(r["cost_cents"] or 0) for r in rows)
    if budget_cents and spent > budget_cents:
        return False, f"Run budget exceeded: spent {spent}¢ of {budget_cents}¢"
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_spent = sum(
        int(r["cost_cents"] or 0)
        for r in rows
        if (r["created_at"] or "").startswith(today_prefix)
    )
    if daily_budget_cents and daily_spent > daily_budget_cents:
        return False, (
            f"Daily budget exceeded: spent {daily_spent}¢ of "
            f"{daily_budget_cents}¢"
        )
    return True, ""


# ── Anomaly detector (PRD §14.4) ──────────────────────────────────

class AnomalyDetector:
    """Detect cost anomalies: single-call spike, daily spike, per-iter spike."""

    def __init__(self, window: int = 10):
        self.window = window
        self.recent_per_call: deque[int] = deque(maxlen=window)
        self.recent_daily: deque[int] = deque(maxlen=7)
        self.recent_per_iter: deque[int] = deque(maxlen=window)

    def observe_call(self, cost_cents: int) -> dict[str, Any] | None:
        """Record a single call's cost. Returns anomaly info if triggered."""
        triggered = None
        avg = (
            sum(self.recent_per_call) / len(self.recent_per_call)
            if self.recent_per_call
            else 0
        )
        if avg > 0 and cost_cents > 3 * avg:
            triggered = {
                "type": "single_call_spike",
                "cost_cents": cost_cents,
                "avg_cents": avg,
            }
        self.recent_per_call.append(int(cost_cents))
        return triggered

    def observe_daily(self, cost_cents: int) -> dict[str, Any] | None:
        """Record end-of-day total. Returns anomaly info if triggered."""
        avg = (
            sum(self.recent_daily) / len(self.recent_daily)
            if self.recent_daily
            else 0
        )
        triggered = None
        if avg > 0 and cost_cents > 2 * avg:
            triggered = {
                "type": "daily_spike",
                "cost_cents": cost_cents,
                "avg_cents": avg,
            }
        self.recent_daily.append(int(cost_cents))
        return triggered

    def observe_iter(self, cost_cents: int) -> dict[str, Any] | None:
        """Record an iter's cost. Returns anomaly info if triggered."""
        avg = (
            sum(self.recent_per_iter) / len(self.recent_per_iter)
            if self.recent_per_iter
            else 0
        )
        triggered = None
        if avg > 0 and cost_cents > 2 * avg:
            triggered = {
                "type": "per_iter_spike",
                "cost_cents": cost_cents,
                "avg_cents": avg,
            }
        self.recent_per_iter.append(int(cost_cents))
        return triggered


# ── Cost event recording ─────────────────────────────────────────

def record_cost(
    db: Database,
    *,
    run_id: str | None,
    phase_job_id: str | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """Record a cost event and return its cost in cents.

    Pricing is taken from model_profiles.yaml. Returns 0 if model unknown.
    """
    profiles = get_model_profiles()
    profile = profiles.get(model, {})
    if not profile:
        cost_cents = 0
    else:
        cost_cents = (
            input_tokens / 1000.0
            * float(profile.get("pricing_per_1k_input", 0))
            + output_tokens / 1000.0
            * float(profile.get("pricing_per_1k_output", 0))
            + cache_read_tokens / 1000.0
            * float(profile.get("pricing_per_1k_cache_read", 0))
            + cache_write_tokens / 1000.0
            * float(profile.get("pricing_per_1k_cache_write", 0))
        ) * 100.0

    cost_id = f"cost-{int(datetime.now(timezone.utc).timestamp() * 1_000_000)}"
    db.execute(
        "INSERT INTO cost_events "
        "(id, run_id, phase_job_id, provider, model, input_tokens, "
        " output_tokens, cache_read_tokens, cache_write_tokens, "
        " cost_cents, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cost_id, run_id, phase_job_id, provider, model,
         input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
         int(cost_cents), now_iso()),
    )
    return int(cost_cents)
