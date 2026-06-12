# Runbook: 10 — Vacation Handoff

## Trigger
The operator is going on vacation for 1+ weeks. The 7×24 system will keep
running unattended; a colleague needs to know what to watch for.

## Pre-departure checklist

- [ ] The system is in a healthy state: `ar724 status` shows
      `status: running` and `last_heartbeat < 60s`.
- [ ] Budget is set conservatively: `ar724 budget set --daily 30`.
- [ ] `oscillation_policy` is `warn` (default) unless you have empirical
      evidence it should be `halt`.
- [ ] The cron tick is active: `crontab -l | grep ares-cron-tick.sh`.
- [ ] The launchd plist is loaded: `launchctl list | grep ztrade-ares-7x24`.
- [ ] The colleague has read access to:
      - `.ares/state.db` (read-only)
      - `ar724/runbooks/` (all 10)
      - The Feishu alert webhook (if applicable)
- [ ] You have briefed the colleague on:
      - The 4 fixed roles and their write scopes
      - The 10 promotion gates
      - The location of `.circuit-breaker` and what to do if it appears
      - The escalation contact (yourself, or a backup)

## During the vacation

The system is designed to run unattended. Common operator actions during
this period:

- If the `.circuit-breaker` file appears: read `.ares/run-events.log` for
  the reason, then follow the relevant runbook.
- If the Feishu alert fires for `budget_exceeded` or `circuit_breaker_tripped`:
  run `ar724 events --since 24h --severity critical` to diagnose.
- If the colleague wants to halt the run: `ar724 halt "vacation" --force`.
  Resume later with `ar724 resume`.

## Post-vacation checklist

- [ ] Run `ar724 metrics` and compare to your pre-departure snapshot.
- [ ] Run `ar724 eval run-all` to confirm the eval suite still passes.
- [ ] If a promotion happened during vacation, run `ar724 explain
      <candidate_hash>` to validate the decision.
- [ ] If the budget was exhausted, raise it and resume.
- [ ] Update the runbooks if any new incident classes were discovered.
