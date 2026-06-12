# Runbook: 00 — Circuit Breaker Tripped (FIRST RESPONSE)

## Trigger
A `circuit_breaker_tripped` event appears in `ar724 events` with severity
`critical`, OR the file `.ares/.circuit-breaker` exists.

This is the **first** runbook an operator should consult during an incident.
Other runbooks (01-10) are referenced from here as needed.

## Detection
- Feishu alert: `[CRITICAL] circuit_breaker_tripped`
- `ar724 events --since 1h --severity critical` shows the event
- `.ares/.circuit-breaker` file exists in the run directory

## Immediate action (under 5 minutes)
1. Read the circuit-breaker file:
   ```bash
   cat .ares/.circuit-breaker
   ```
2. Identify the cause from the file content (format: `halted at <iso>: <reason>`).
3. Read the most recent events:
   ```bash
   ar724 events --since 1h --severity critical
   ```
4. Based on the cause, jump to the relevant runbook:
   - budget exceeded → runbook 06 (cost spike)
   - consecutive_discards → runbook 09 (extend leases) or 10 (vacation handoff)
   - oscillation halt → runbook 08 (sqlite snapshot) + review recent candidates
   - safety violation → runbook 03 (disable MCP server)
   - manual halt → runbook 01 (pause/resume)
5. **Do NOT immediately resume.** Investigate the cause first.

## Diagnosis
- What event triggered the circuit breaker?
- Is this the first time, or has it happened before?
- Were there warnings in the hours before the trip?

```bash
ar724 events --since 24h --severity warn
ar724 status --verbose
```

## Recovery
After the cause is addressed:
1. Clear the circuit-breaker file:
   ```bash
   rm .ares/.circuit-breaker
   ```
2. Resume the run:
   ```bash
   ar724 resume --force
   ```
3. Watch the next 1-2 iterations to confirm the system is healthy.

## Postmortem checklist
- [ ] Document the trigger cause in `.ares/run-events.log`.
- [ ] If the cause was budget: consider raising the daily cap or pausing overnight.
- [ ] If the cause was oscillation: review the detector's threshold (`ar724 oscillation set-policy`).
- [ ] If the cause was a safety violation: add a fixture to `evals/prompt_injection/`.
- [ ] Update the relevant runbook if the cause was not covered.
