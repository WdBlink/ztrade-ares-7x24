# Runbook: 01 — Pause and Resume

## Trigger
The system is producing too many iterations, spending too fast, or
interfering with a manual experiment. The user wants to stop new iterations
without killing in-flight work.

## Detection
- `ar724 status` shows `status: running` and a high `iterations.completed.last_24h`.
- The board shows repeated `iter_queued` events while the operator is working.
- The user explicitly asks "pause the run".

## Immediate action
1. Run `ar724 pause`.
2. Confirm with `ar724 status` that `status` is now `paused`.
3. Watch the board for 1 minute to confirm no new iterations are queued.

## Diagnosis
- Why is the run over-producing? Check `ar724 events --since 1h --severity warn`.
- Common causes: budget too high, stop_conditions too loose, goal unclear.

## Recovery
1. Adjust the relevant field in `.ares/loop_config.json`:
   - Lower `stop_conditions.max_iterations`
   - Lower `resource_budget.max_daily_token_usd`
   - Tighten the `goal` text
2. Run `ar724 config reload` (sends SIGHUP to the conductor).
3. Run `ar724 resume`.

## Postmortem checklist
- [ ] Log the pause reason in `.ares/run-events.log`.
- [ ] Note any config changes in the next weekly review.
- [ ] If the run was halted for budget, verify the next run has a daily cap.
