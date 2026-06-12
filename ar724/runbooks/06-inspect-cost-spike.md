# Runbook: 06 — Inspect a Cost Spike

## Trigger
A cost-anomaly event fires (PRD §14.4). The board shows daily cost is 2x
the 7-day average, or a single call is 3x the role's 10-call moving average.

## Detection
- `ar724 events --since 1h` shows `event_type = cost_anomaly`.
- The board shows `cost.cents.today` jumping suddenly.

## Immediate action
1. Identify the offending call:
   `ar724 costs --today` → note the model and call count.
2. Check the recent phase_jobs to find the call:
   `ar724 events --since 1h --severity warn` → look for a `cost_anomaly`
   with `cost_cents` and `phase_job_id`.
3. Run `ar724 trace <trace_id>` to see the full iter timeline.

## Diagnosis
- Common causes:
  - A role's `default_profile` was auto-downgraded then auto-upgraded
    (oscillation in routing).
  - A new role with an expensive model was added.
  - A worker hit the `max_tokens_per_call` cap and retried with a larger context.
  - A worker got into a long loop (10k+ output tokens).

## Recovery
1. If the spike is from a single bad call: no action needed. The cost is
   already incurred; the system has self-corrected.
2. If the spike is from a runaway worker: run `ar724 iter cancel` and let
   the next iter re-spawn.
3. If the spike is from routing oscillation: lock the profile in
   `config/role_routing.yaml` (set `escalation_profile: null`).

## Postmortem checklist
- [ ] If the anomaly was real, raise the operator's `cost.cents.today` cap
       and adjust the next 7-day average baseline.
- [ ] If the anomaly was a false positive (e.g. legitimate long call), add
       a `cost-override` to the run's audit log.
