# Runbook: 03 — Disable an MCP Server

## Trigger
An MCP server in the allowlist is producing errors, returning prompt-injection
content, or has been deprecated. The operator needs to take it out of
rotation immediately.

## Detection
- `ar724 events --since 1h --severity error` shows repeated `safety_violation_blocked`
  events tied to a specific MCP server.
- The board shows repeated `phase_failed` events with `error_class = "mcp_error"`.
- A user reports suspicious output from a worker that uses MCP.

## Immediate action
1. **Stop the run**:
   `ar724 halt "mcp_server=<name> disabled" --force`
2. Edit `config/safety_policy.yaml` and remove the offending server from
   `mcp_allowlist`. Save.
3. Run `ar724 safety policy reload` — this writes a row to the `approvals`
   table (operator-level action).

## Diagnosis
- Why was the server misbehaving? Check the server's logs if accessible.
- If the issue is prompt injection: see PRD §15.3 for handling patterns.
- If the issue is the server itself: contact the maintainer or replace it.

## Recovery
1. Once the operator has decided the server is safe to re-enable (or has
   been replaced), re-add it to `mcp_allowlist` in `safety_policy.yaml`.
2. Run `ar724 safety policy reload` again.
3. Run `ar724 resume` to continue the run.

## Postmortem checklist
- [ ] Document the incident in `.ares/run-events.log`.
- [ ] If prompt injection was the cause, add a fixture to
       `evals/prompt_injection/` so the eval suite catches it.
- [ ] If the server is permanently removed, update `config/role_routing.yaml`
       so dependent roles use a fallback.
