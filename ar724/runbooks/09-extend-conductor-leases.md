# Runbook: 09 — Extend Conductor Leases

## Trigger
A long-running iteration (e.g. a 30-minute backtest) is being prematurely
reaped by the stale-reaper because the conductor's 180s lease expires
mid-backtest.

## Detection
- `ar724 events --since 1h` shows `phase_orphaned` for a phase job whose
  `last_heartbeat_at` is recent.
- The worker is still running but the slot is marked `failed` in SQLite.

## Immediate action
1. **Do not panic-kill the worker.** The worker is still producing useful
   output; killing it wastes tokens.
2. Read the conductor lease constants in `ar724/conductor.py`:
   `LOCK_LEASE_SECONDS = 180` and the HeartbeatTimer's `interval_s = 3.0`.
3. Verify the worker is calling `ar724/heartbeat.py:HeartbeatTimer`:
   the timer should be wrapping the long task. If it's not, the timer is
   not installed — that's the bug.

## Diagnosis
- If the timer IS installed but heartbeats are not reaching SQLite: the
  SQLite connection is blocked by a long transaction. Restart the
  conductor (`ar724 down; ar724 up`) to release the connection.
- If the timer is NOT installed: this is a bug. File a task to wrap the
  backtester with `HeartbeatTimer`.

## Recovery
1. As a temporary fix, edit `ar724/conductor.py` and increase
   `LOCK_LEASE_SECONDS` to e.g. 600.
2. Restart the conductor: `ar724 down; ar724 up`.
3. Watch the next long backtest: it should no longer be reaped.

## Postmortem checklist
- [ ] If the timer was missing, fix the bug and add a fixture to
       `evals/evaluator_correctness/` that asserts a 30-min backtest
       writes heartbeats every 3s.
- [ ] Document the temporary lease extension in `.ares/run-events.log`.
