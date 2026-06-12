# autoresearch v2 — Research Protocol

This document is the human-readable protocol for a single iteration of the
ztrade-ares 7×24 research loop. The four role definitions (formerly §1 of
this file) have moved to `autoresearch/v2/roles/*.yaml` per V1.0 PRD §7.3.

## §1. Roles

See `roles/factor_combiner.yaml`, `roles/backtester.yaml`,
`roles/factor_validator.yaml`, `roles/backtest_reviewer.yaml`. Each YAML
defines `system_prompt`, `tools`, `skills`, `max_iterations`,
`timeout_seconds`, `max_retries`, and `acceptance` (PRD §7.3 / §9.2).

## §2. Mutable surface

`autoresearch/mutable/v47_params.json` is the candidate under evaluation.
Only the `backtester` role may write it. The controller is the only writer
of `autoresearch/best/v47_params.json` (the promoted winner).

## §3. Iteration sequence (DAG, PRD §6.2 / §6.3)

```
queued → proposing (L1) → building (L2) → validating (L3)
       → evaluating (L4, deterministic)
       → reviewing (L4, parallel with promoting-eligibility)
       → promoting (L5, controller-internal)
       → completed
```

The DAG is computed at startup from the 4 role YAMLs (PRD §6.3). Within
each layer, jobs may run in parallel; today the 4 fixed roles serialize.

## §4. Promotion (PRD §8)

Ten mechanical gates (PRD §8.1) must all pass for a candidate to be promoted
to `autoresearch/best/`. The promotion is an idempotent transaction
(PRD §8.2) recorded in the `promotions` SQLite table with a unique
`idempotency_key`.

## §5. Stop conditions

Configured in `.ares/loop_config.json` under `stop_conditions`. The conductor
checks these at every tick; on hit, the run is marked `completed` and the
controller stops scheduling new iterations.

## §6. Failure handling

See PRD §11.4 (error table) and `ar724/runbooks/*.md` for operator actions.
The stale-reaper (PRD §5.3) and the cron tick (PRD §4.2) are the safety net.

## §7. License and attribution

Vibe-Trading (MIT) ports and ClawTeam/OPC design patterns are credited in
the project README. Source code attribution headers are on every ported file.
