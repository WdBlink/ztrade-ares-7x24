# Runbook: 07 — Recover Orphaned Jobs

## Trigger
The stale-reaper (PRD §5.3) has marked phase jobs as `orphaned_process`.
The board shows a sudden drop in `workers.active.current` and a spike in
`phase_orphaned` events.

## Detection
- `ar724 events --since 1h` shows `event_type = phase_orphaned`.
- `ar724 status` shows `last_heartbeat > 60s` for an active phase job.

## Immediate action
1. Identify the affected jobs:
   `ar724 events --since 1h --severity warn` → filter on `phase_orphaned`.
2. Check if the underlying tmux pane is alive:
   `tmux list-panes -t ar7x24-<run_id>:<slot_name>`
3. If the pane is dead but the lease is still active, the controller will
   reap it on the next tick (5s).

## Diagnosis
- Common causes:
  - Worker process crashed (segfault, OOM).
  - tmux session was killed manually (`tmux kill-session`).
  - macOS put the worker pane to sleep (rare; check Energy settings).

## Recovery
1. The reaper marks the job as `failed` and requeues it for retry.
2. Verify the retry fired: `ar724 events --since 5m` should show a new
   `phase_queued` event for the same role.
3. If retries are exhausted, the job stays `failed` and the iteration
   may need a manual `ar724 iter cancel` followed by `ar724 iter next`.

## Postmortem checklist
- [ ] If the same job crashes repeatedly, file a bug against the role.
- [ ] If tmux is dying, check launchd's `KeepAlive` settings.
- [ ] If the crash is OOM, lower the worker's `max_iterations`.
