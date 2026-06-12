"""Oscillation detector — quant-domain A↔B↔A pattern detection.

This module is an INDEPENDENT RE-IMPLEMENTATION of an L3 quality-control
pattern; no source code is copied from any external system.

The pattern is "inspired by" OPC v0.8 (iamtouchskyer/opc) — specifically the
naive per-parameter oscillation detector described in OPC-One-Person-Company.md:115.
Vibe-Trading does not have an equivalent pattern.

Two complementary signals reduce false positives in the quant domain:
  1. Per-parameter oscillation (any single parameter oscillating 4+ times in 6 iters)
  2. Joint structural oscillation (after bucketing continuous params to 3 decimals)

The naive version hashes the full candidate_params JSON, which in a quant
domain with continuous parameters produces high false-positive rates (5% weight
change is semantically equivalent but produces a different hash).

PRD §9.1.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping


class OscillationDetector:
    """Detect A↔B↔A↔B pattern in candidate proposals.

    The detector is consulted at the controller's `promote` step, after gates
    1-8 pass and before the `git commit`. If the detector returns a non-empty
    dict, the controller logs the detection to `events` and either warns
    (default) or halts (if `oscillation_policy = 'halt'` in loop_config.json).
    """

    def __init__(
        self,
        window: int = 6,
        per_param_threshold: int = 4,
        joint_max_unique: int = 2,
        bucket_decimals: int = 3,
    ):
        self.window = window
        self.per_param_threshold = per_param_threshold
        self.joint_max_unique = joint_max_unique
        self.bucket_decimals = bucket_decimals
        self.history: deque[str] = deque(maxlen=window)
        self.param_history: dict[str, deque[str]] = {}

    @staticmethod
    def _bucket(value, decimals: int = 3) -> str:
        """Bucket continuous values to a fixed decimal precision.

        Discrete values (strings, bools) pass through unchanged.
        """
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:.{decimals}f}"
        return str(value)

    @staticmethod
    def _structural_hash(params: Mapping) -> str:
        """Hash over bucketed values; ignores tiny continuous perturbations."""
        bucketed = {
            k: OscillationDetector._bucket(v)
            for k, v in sorted(params.items())
        }
        return hashlib.sha256(
            json.dumps(bucketed, sort_keys=True).encode()
        ).hexdigest()[:16]

    def observe(self, candidate_params: Mapping) -> dict[str, str]:
        """Observe a candidate. Returns a dict of triggered detections.

        Empty dict = no oscillation detected. Non-empty = at least one signal
        fired.
        """
        triggered: dict[str, str] = {}

        # 1. Joint structural oscillation
        sh = self._structural_hash(candidate_params)
        self.history.append(sh)
        if len(self.history) >= self.window:
            recent = list(self.history)[-self.window:]
            unique_count = len(set(recent))
            if unique_count <= self.joint_max_unique:
                triggered["joint_structural"] = (
                    f"Window of {self.window} candidates hashes to "
                    f"{unique_count} unique structural states"
                )

        # 2. Per-parameter oscillation
        for k, v in candidate_params.items():
            bucketed = self._bucket(v, decimals=self.bucket_decimals)
            if k not in self.param_history:
                self.param_history[k] = deque(maxlen=self.window)
            self.param_history[k].append(bucketed)
            if len(self.param_history[k]) >= self.window:
                recent = list(self.param_history[k])[-self.window:]
                unique_vals = set(recent)
                last = recent[-1]
                # Same value appearing 4+ times in 6 iters = oscillating
                if (
                    len(unique_vals) <= 2
                    and recent.count(last) >= self.per_param_threshold
                ):
                    triggered[f"per_param_{k}"] = (
                        f"Parameter {k} oscillates between {sorted(unique_vals)} "
                        f"({recent.count(last)} times at {last} in last "
                        f"{self.window} iters)"
                    )

        return triggered

    def reset(self) -> None:
        """Clear all history (e.g. on run reset)."""
        self.history.clear()
        self.param_history.clear()

    @property
    def is_empty(self) -> bool:
        return len(self.history) == 0 and not self.param_history
