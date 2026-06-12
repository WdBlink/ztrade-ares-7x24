# Runbook: 04 — Rotate Secrets

## Trigger
A credential in `.ares/env.sh` has been leaked, an upstream provider
rotates its API key, or a scheduled rotation is due.

## Detection
- A 401/403 from the LLM provider appears in worker stderr.
- A secret scanner (e.g. gitleaks) flags a reference in the repo.
- A scheduled rotation reminder fires.

## Immediate action
1. **Do not commit the new secret.** `.ares/env.sh` is gitignored.
2. Edit `.ares/env.sh` and replace the old value with the new one.
   The file is sourced at controller startup; SIGHUP does NOT re-source it.
3. Run `ar724 halt "secret rotation" --force` to stop the conductor.
4. Run `ar724 up` to restart (re-sources env.sh, restarts the controller).

## Diagnosis
- Which secret was leaked? Check the scanner's report.
- If the leak was via a worker output (e.g. a worker echoed a token), audit
  the worker prompt and tighten the safety policy.

## Recovery
1. Once the new secret is verified working, run `ar724 resume` to continue.
2. If the secret is also referenced in cron or launchd plists, update those.

## Postmortem checklist
- [ ] Update `.ares/env.sh.example` with placeholder names (no real values).
- [ ] If the secret was leaked via worker output, add a redaction check to
       `ar724/safety.py` and add a fixture to `evals/prompt_injection/`.
- [ ] If the rotation was not on schedule, add a calendar reminder.
