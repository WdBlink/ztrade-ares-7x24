# Runbook: 08 — Restore a SQLite Snapshot

## Trigger
SQLite WAL corruption is suspected (rare, but possible on hard reboot or
disk full). The conductor fails to open `.ares/state.db`.

## Detection
- `ar724 status` exits with "database disk image is malformed".
- The cron tick logs `sqlite3.DatabaseError: database disk image is malformed`.
- `sqlite3 .ares/state.db ".schema"` returns an error.

## Immediate action
1. **Stop the run**:
   `ar724 halt "sqlite corruption; restoring snapshot" --force`
2. List available snapshots:
   `ar724 backup list`
3. Pick the most recent valid snapshot (typically the daily snapshot from
   yesterday).
4. Restore:
   `ar724 backup restore <YYYY-MM-DD> --confirm`

## Diagnosis
- Why did SQLite corrupt? Common causes:
  - Hard reboot (Ctrl-C during a write).
  - Disk full (write returned but file is partial).
  - Bad block on the underlying SSD.
- If the corruption recurs, switch to a more durable filesystem or move
  `.ares/` to a different disk.

## Recovery
1. After restore, verify the schema is intact:
   `sqlite3 .ares/state.db ".schema" | head -5`
2. Run `ar724 up` to restart. The restored snapshot is the new source of
   truth; the system does not re-apply events that happened after the
   snapshot (PRD §15.5).
3. Run `ar724 status` to confirm the run is recognized.

## Postmortem checklist
- [ ] Document the corruption cause in `.ares/run-events.log`.
- [ ] If disk-full was the cause, add disk-usage monitoring.
- [ ] If the corruption is reproducible, file a task to investigate.
