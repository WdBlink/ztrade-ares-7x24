"""Tmux manager tests — session existence, window enumeration (no real tmux spawn).

These tests exercise pure-Python helpers; the actual tmux subprocess calls
are exercised manually (tmux may not be available in the CI sandbox).
"""

from __future__ import annotations

from ar724.tmux_manager import (
    SLOT_WINDOW_INDICES, WORKER_SLOTS, is_pid_alive, session_name,
)


def test_session_name_sanitizes_special_chars():
    """Session names are restricted to alnum + dash + underscore."""
    assert session_name("run-abc") == "ar7x24-run-abc"
    # Special characters get replaced
    assert session_name("run/with:bad*chars") == "ar7x24-run-with-bad-chars"


def test_session_name_preserves_dashes():
    assert session_name("2026-06-12-iter-47") == "ar7x24-2026-06-12-iter-47"


def test_worker_slots_canonical_4():
    """The 4 fixed role slots are the canonical V1.0 layout."""
    assert set(WORKER_SLOTS) == {
        "proposer", "builder", "validator", "reviewer",
    }


def test_slot_window_indices_unique():
    """Each worker slot has a unique window index."""
    indices = list(SLOT_WINDOW_INDICES.values())
    assert len(indices) == len(set(indices))


def test_is_pid_alive_returns_false_for_invalid_pid():
    assert is_pid_alive(None) is False
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False


def test_is_pid_alive_returns_true_for_self():
    import os
    assert is_pid_alive(os.getpid()) is True
