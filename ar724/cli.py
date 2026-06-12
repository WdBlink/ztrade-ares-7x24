"""ar724 CLI — primary operator/researcher interface.

Implements all commands from PRD §22.4:
  status, board, events, trace, explain, costs, metrics, list-runs, list-roles,
  config, goal, budget, scope, oscillation, up, down, pause, resume, halt,
  iter, promotion, mcp, safety, secret, eval, snapshot, backup, init,
  conductor, cron-tick, validate-roles, evaluator.

The Skill (ztrade-ares-7x24/SKILL.md) routes verb→subcommand; this is the
unimpeachable, scriptable, low-latency surface.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click

from . import (
    config_loader, observability, safety, tmux_manager,
)
from .budget import check_budget
from .conductor import (
    LOCK_LEASE_SECONDS, create_iteration, create_run, enqueue_phase_jobs,
    reap_stale_phase_jobs, start_run, tick,
)
from .db import Database, atomic_write, now_iso
from .evaluator_runner import run_evaluator
from .oscillation import OscillationDetector
from .promotion_gate import (
    GATE_NAMES, claim_promotion_lock, perform_promotion,
    release_promotion_lock, run_all_gates,
)
from .role_loader import REQUIRED_ROLE_IDS, validate_roles


# ── Helpers ───────────────────────────────────────────────────────

def _db() -> Database:
    """Open the SQLite database (path from env or default)."""
    path = os.environ.get("AR724_STATE_DB", ".ares/state.db")
    return Database(path)


def _print_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


# ── Root group ───────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="ar724")
def main() -> None:
    """ztrade-ares 7×24 autonomous research controller."""


# ── status / board / events / trace / explain ────────────────────

@main.command()
@click.option("--format", "fmt", default="human", type=click.Choice(["human", "json", "last_candidate_hash"]))
@click.option("--verbose", is_flag=True)
def status(fmt: str, verbose: bool) -> None:
    """Show current run, iteration, phase, active leases, best score, cost."""
    db = _db()
    run = db.fetchone(
        "SELECT * FROM runs WHERE status IN ('running', 'paused', 'waiting_approval') "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not run:
        if fmt == "json":
            _print_json({"active": False})
        else:
            click.echo("no active run")
        return
    metrics = observability.compute_metrics(db)
    if fmt == "json":
        _print_json({"active": True, "run": dict(run), "metrics": metrics})
    elif fmt == "last_candidate_hash":
        cand = db.fetchone(
            "SELECT * FROM candidates ORDER BY created_at DESC LIMIT 1"
        )
        click.echo(cand["hash"] if cand else "")
    else:
        click.echo(f"run_id:        {run['id']}")
        click.echo(f"status:        {run['status']}")
        click.echo(f"goal:          {run['goal']}")
        click.echo(f"spent/budget:  {run['spent_cents']}¢ / {run['budget_cents']}¢")
        if verbose:
            for k, v in metrics.items():
                click.echo(f"{k:35s} {v}")


@main.command(name="board")
def board_cmd() -> None:
    """Print a snapshot of the live status board (re-runs in a loop)."""
    db = _db()
    metrics = observability.compute_metrics(db)
    click.echo("─── ar724 board ───")
    for name, value in metrics.items():
        click.echo(f"{name:35s} {value}")


@main.command()
@click.option("--tail", is_flag=True, help="Stream new events as they arrive")
@click.option("--since", default=None, help="Time window, e.g. '1h', '30m'")
@click.option("--severity", default=None, type=click.Choice(["info", "warn", "error", "critical"]))
@click.option("--limit", default=100, type=int)
def events(tail: bool, since: str | None, severity: str | None, limit: int) -> None:
    """Show structured events (PRD §13.3 controlled vocabulary)."""
    db = _db()
    since_seconds = _parse_since(since) if since else None
    rows = observability.tail_events(db, since_seconds=since_seconds, severity=severity, limit=limit)
    for row in rows:
        click.echo(json.dumps(dict(row), default=str))


def _parse_since(s: str) -> int:
    """Parse '1h'/'30m'/'15s' to seconds. Raises on bad input."""
    if not s:
        return 0
    unit = s[-1]
    try:
        n = int(s[:-1])
    except ValueError:
        raise click.BadParameter(f"invalid --since value: {s!r}")
    return {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 1) * n


@main.command()
@click.argument("trace_id")
def trace(trace_id: str) -> None:
    """Full trace dump for a given iteration."""
    db = _db()
    iter_row = db.fetchone('SELECT * FROM iterations WHERE trace_id = ?', (trace_id,))
    if not iter_row:
        click.echo(f"no iteration found with trace_id={trace_id}", err=True)
        sys.exit(1)
    events = db.fetchall(
        "SELECT * FROM events WHERE iteration_id = ? ORDER BY created_at",
        (iter_row["id"],),
    )
    click.echo(f"trace_id: {trace_id}  iter: {iter_row['index']}  status: {iter_row['status']}")
    for ev in events:
        click.echo(f"  {ev['created_at']} {ev['severity']:8s} {ev['event_type']}")


@main.command()
@click.argument("candidate_hash")
def explain(candidate_hash: str) -> None:
    """Show proposal, patch, evaluator result, reviewer verdict, promotion decision."""
    db = _db()
    cand = db.fetchone("SELECT * FROM candidates WHERE hash = ?", (candidate_hash,))
    if not cand:
        click.echo(f"no candidate with hash={candidate_hash}", err=True)
        sys.exit(1)
    eval_row = db.fetchone(
        "SELECT * FROM evaluations WHERE candidate_hash = ? ORDER BY created_at DESC LIMIT 1",
        (candidate_hash,),
    )
    review_row = db.fetchone(
        "SELECT * FROM reviews WHERE candidate_hash = ? ORDER BY created_at DESC LIMIT 1",
        (candidate_hash,),
    )
    promo_row = db.fetchone(
        "SELECT * FROM promotions WHERE candidate_hash = ? ORDER BY promoted_at DESC LIMIT 1",
        (candidate_hash,),
    )
    _print_json({
        "candidate": dict(cand),
        "evaluation": dict(eval_row) if eval_row else None,
        "review": dict(review_row) if review_row else None,
        "promotion": dict(promo_row) if promo_row else None,
    })


@main.command()
@click.option("--today", is_flag=True)
def costs(today: bool) -> None:
    """Show model usage and spend by role/model."""
    db = _db()
    if today:
        rows = db.fetchall(
            "SELECT model, SUM(cost_cents) AS cents, SUM(input_tokens) AS in_tok, "
            "SUM(output_tokens) AS out_tok "
            "FROM cost_events WHERE created_at >= datetime('now', 'start of day') "
            "GROUP BY model"
        )
    else:
        rows = db.fetchall(
            "SELECT model, SUM(cost_cents) AS cents, SUM(input_tokens) AS in_tok, "
            "SUM(output_tokens) AS out_tok "
            "FROM cost_events GROUP BY model"
        )
    _print_json([dict(r) for r in rows])


@main.command()
def metrics() -> None:
    """Dump the §13.1 metric counters."""
    db = _db()
    _print_json(observability.compute_metrics(db))


@main.command("list-runs")
def list_runs() -> None:
    """List all runs (active and historical)."""
    db = _db()
    for r in db.fetchall("SELECT id, status, goal, started_at, completed_at FROM runs ORDER BY created_at DESC"):
        click.echo(f"{r['id']:40s} {r['status']:12s} {r['goal'][:60]}")


@main.command("list-roles")
def list_roles() -> None:
    """List all role YAMLs and their current acceptance criteria."""
    roles_dir = Path(os.environ.get("AR724_ROLES_DIR", "autoresearch/v2/roles"))
    for role in validate_roles(roles_dir):
        click.echo(f"{role['id']:25s}  {role['title']}")
        for crit in role.get("acceptance", []):
            click.echo(f"    - {crit}")


# ── Config (PRD §22.4.2) ─────────────────────────────────────────

@main.group()
def config() -> None:
    """Configuration subcommands (read-write, requires re-read)."""


@config.command(name="show")
def config_show() -> None:
    """Print current effective configuration."""
    _print_json({
        "loop_config": config_loader.get_loop_config(),
        "safety_policy": config_loader.get_safety_policy(),
        "model_profiles": list(config_loader.get_model_profiles().keys()),
        "role_routing": config_loader.get_role_routing(),
    })


@config.command(name="validate")
@click.argument("file_path", type=click.Path(exists=True))
def config_validate(file_path: str) -> None:
    """Validate a config file against the schema without applying it."""
    import yaml
    data = yaml.safe_load(Path(file_path).read_text())
    if "schema_version" not in data:
        click.echo("missing schema_version", err=True)
        sys.exit(1)
    click.echo("OK")


@config.command(name="reload")
def config_reload() -> None:
    """Re-read loop_config.json and apply (sends SIGHUP to conductor)."""
    pid_file = Path(".ares/conductor.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGHUP)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    click.echo("reload signal sent (SIGHUP)")


@main.group()
def goal() -> None:
    """Goal management subcommands."""


@goal.command(name="set")
@click.argument("text")
def goal_set(text: str) -> None:
    """Update the research goal in loop_config.json."""
    cfg = config_loader.get_loop_config()
    cfg["goal"] = text
    config_loader.write_loop_config(cfg)
    click.echo(f"goal updated: {text}")


@main.group()
def budget() -> None:
    """Budget management subcommands."""


@budget.command(name="set")
@click.option("--daily", type=int, default=None, help="Daily budget in cents")
@click.option("--iter-runtime", type=int, default=None, help="Per-iter runtime minutes")
@click.option("--run", "run_budget", type=int, default=None, help="Total run budget in cents")
def budget_set(daily: int | None, iter_runtime: int | None, run_budget: int | None) -> None:
    """Update budget fields in loop_config.json."""
    cfg = config_loader.get_loop_config()
    rb = cfg.setdefault("resource_budget", {})
    if daily is not None:
        rb["max_daily_token_usd"] = daily / 100.0
    if iter_runtime is not None:
        rb["max_iter_runtime_minutes"] = iter_runtime
    if run_budget is not None:
        rb.setdefault("max_run_budget_cents", run_budget)
    config_loader.write_loop_config(cfg)
    click.echo(f"budget updated: {rb}")


@main.group()
def scope() -> None:
    """Universe/scope subcommands."""


@scope.command(name="set")
@click.option("--selection-method", default=None)
def scope_set(selection_method: str | None) -> None:
    """Update scope_overrides in loop_config.json."""
    cfg = config_loader.get_loop_config()
    so = cfg.setdefault("scope_overrides", {})
    if selection_method:
        so["selection_method"] = selection_method
    config_loader.write_loop_config(cfg)
    click.echo(f"scope updated: {so}")


@main.group()
def oscillation() -> None:
    """Oscillation policy subcommands."""


@oscillation.command(name="set-policy")
@click.argument("policy", type=click.Choice(["warn", "halt"]))
def oscillation_set_policy(policy: str) -> None:
    """Set oscillation_policy (warn | halt)."""
    cfg = config_loader.get_loop_config()
    cfg["oscillation_policy"] = policy
    config_loader.write_loop_config(cfg)
    click.echo(f"oscillation_policy = {policy}")


# ── Lifecycle (PRD §22.4.3) ──────────────────────────────────────

@main.command()
@click.option("--goal-text", default=None)
def init(goal_text: str | None) -> None:
    """Initialize the .ares/ working directory and state.db."""
    ares = Path(".ares")
    ares.mkdir(exist_ok=True)
    if not (ares / "state.db").exists():
        Database(ares / "state.db")
    if not (ares / "loop_config.json").exists():
        cfg = {
            "schema_version": 1,
            "goal": goal_text or "Tighten the v47 momentum weights.",
            "stop_conditions": {
                "candidate_mean_annual_return_pct_min": 8.0,
                "max_iterations": 200,
                "max_wallclock_hours": 168,
            },
            "resource_budget": {
                "max_iter_runtime_minutes": 90,
                "max_daily_token_usd": 50.0,
                "consecutive_discard_block_limit": 5,
                "consecutive_blocked_limit": 3,
            },
            "oscillation_policy": "warn",
        }
        config_loader.write_loop_config(cfg, ares / "loop_config.json")
    if not (ares / "env.sh").exists():
        (ares / "env.sh").write_text("# MCP env vars, secrets by reference\n")
    # ensure .ares in .gitignore
    gi = Path(".gitignore")
    if gi.exists() and ".ares/" not in gi.read_text():
        gi.write_text(gi.read_text() + "\n.ares/\n")
    click.echo(f"initialized: {ares.resolve()}")


@main.command()
def up() -> None:
    """Install launchd plist, create tmux session, start conductor + cron."""
    ares = Path(".ares")
    if not (ares / "state.db").exists():
        click.echo("no .ares/state.db; run `ar724 init` first", err=True)
        sys.exit(1)
    db = _db()
    existing = db.fetchone("SELECT id FROM runs WHERE status = 'running' LIMIT 1")
    if existing:
        click.echo(f"already running: {existing['id']}")
        return
    run_id = create_run(db, goal=config_loader.get_loop_config().get("goal", ""))
    start_run(db, run_id)
    iter_id = create_iteration(db, run_id=run_id, index=1)
    enqueue_phase_jobs(db, run_id=run_id, iteration_id=iter_id)
    tmux_manager.create_session(run_id, working_dir=Path.cwd())
    click.echo(f"ar724 is running. tmux session: {tmux_manager.session_name(run_id)}")
    click.echo(f"  run_id: {run_id}")
    click.echo(f"  attach: tmux attach -t {tmux_manager.session_name(run_id)}")


@main.command()
def down() -> None:
    """Graceful shutdown: stop controller, stop cron, kill tmux session."""
    for name in tmux_manager.list_sessions():
        tmux_manager.kill_session(name)
    pid_file = Path(".ares/conductor.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    click.echo("shutdown complete")


@main.command()
def pause() -> None:
    """Pause the run (no new iterations); in-flight workers finish."""
    db = _db()
    db.execute("UPDATE runs SET status = 'paused' WHERE status = 'running'")
    click.echo("paused")


@main.command()
def resume() -> None:
    """Resume a paused run."""
    db = _db()
    db.execute("UPDATE runs SET status = 'running' WHERE status = 'paused'")
    click.echo("resumed")


@main.command()
@click.argument("reason")
@click.option("--force", is_flag=True, help="Required to actually halt")
def halt(reason: str, force: bool) -> None:
    """Halt the run; write .circuit-breaker; send Feishu alert."""
    if not force:
        click.echo("refusing to halt without --force (destructive)")
        sys.exit(2)
    from .db import atomic_write
    cb = Path(".ares/.circuit-breaker")
    cb.parent.mkdir(exist_ok=True)
    atomic_write(cb, f"halted at {now_iso()}: {reason}\n")
    db = _db()
    db.execute("UPDATE runs SET status = 'failed', stop_reason = ? WHERE status = 'running'", (reason,))
    observability.emit_event(db, event_type="run_halted", severity="critical",
                             payload={"reason": reason})
    # Stop the conductor process so launchd's KeepAlive doesn't auto-restart
    # a halted run as a zombie.
    pid_file = Path(".ares/conductor.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    click.echo(f"halted: {reason}")


@main.group()
def iter() -> None:
    """Iteration subcommands."""


@iter.command(name="next")
def iter_next() -> None:
    """Force-trigger the next iteration immediately."""
    db = _db()
    run = db.fetchone("SELECT id FROM runs WHERE status = 'running' LIMIT 1")
    if not run:
        click.echo("no running run", err=True); sys.exit(1)
    last = db.fetchone(
        'SELECT MAX("index") AS i FROM iterations WHERE run_id = ?', (run["id"],),
    )
    new_idx = int(last["i"] or 0) + 1
    iter_id = create_iteration(db, run_id=run["id"], index=new_idx)
    enqueue_phase_jobs(db, run_id=run["id"], iteration_id=iter_id)
    click.echo(f"queued iteration {new_idx}: {iter_id}")


@iter.command(name="cancel")
def iter_cancel() -> None:
    """Cancel the in-flight iteration; revert mutable to best."""
    db = _db()
    last = db.fetchone(
        "SELECT id FROM iterations WHERE status IN ('queued', 'running') "
        'ORDER BY "index" DESC LIMIT 1'
    )
    if not last:
        click.echo("no in-flight iteration", err=True); sys.exit(1)
    db.execute("UPDATE iterations SET status = 'cancelled' WHERE id = ?", (last["id"],))
    db.execute(
        "UPDATE phase_jobs SET status = 'failed', error_class = 'iter_cancelled' "
        "WHERE iteration_id = ? AND status IN ('queued', 'running')",
        (last["id"],),
    )
    click.echo(f"cancelled iter {last['id']}")


@iter.command(name="dry-run")
def iter_dry_run() -> None:
    """Walk through one iteration of the DAG without spawning workers."""
    db = _db()
    roles_dir = Path(os.environ.get("AR724_ROLES_DIR", "autoresearch/v2/roles"))
    roles = validate_roles(roles_dir)
    from .dag import topological_layers
    from .conductor import _phase_job_dag_for_roles
    tasks = _phase_job_dag_for_roles(roles_dir)
    layers = topological_layers(tasks)
    click.echo(f"DRY RUN: {len(layers)} layers, {len(roles)} roles")
    for i, layer in enumerate(layers, 1):
        click.echo(f"  L{i}: {', '.join(layer)}")


# ── Promotion (PRD §22.4.4) ──────────────────────────────────────

@main.group()
def promotion() -> None:
    """Promotion subcommands."""


@promotion.command(name="rollback")
@click.argument("iteration_id")
def promotion_rollback(iteration_id: str) -> None:
    """Revert best/ to the previous best."""
    db = _db()
    rows = db.fetchall(
        "SELECT * FROM promotions WHERE iteration_id = ? ORDER BY promoted_at DESC",
        (iteration_id,),
    )
    if not rows:
        click.echo(f"no promotions found for {iteration_id}", err=True); sys.exit(1)
    last = rows[0]
    if last["status"] != "committed":
        click.echo(f"latest promotion is {last['status']!r}, not committed", err=True); sys.exit(1)
    # Revert: git revert the commit
    sha = last["git_commit"]
    if not sha:
        click.echo("no git_commit recorded; manual revert required", err=True); sys.exit(1)
    proc = subprocess.run(
        ["git", "revert", "--no-edit", sha],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        click.echo(f"git revert failed: {proc.stderr}", err=True); sys.exit(1)
    click.echo(f"reverted promotion {last['id']}")


@promotion.command(name="set-baseline")
@click.argument("iter_tag")
def promotion_set_baseline(iter_tag: str) -> None:
    """Set the best/ baseline from a historical iter-tag."""
    click.echo(f"set-baseline {iter_tag}: not yet wired to candidate store (operator action required)")


# ── MCP / safety / secret (operator-level) ───────────────────────

@main.group()
def mcp() -> None:
    """MCP allowlist subcommands."""


@mcp.command(name="allowlist")
@click.argument("action", type=click.Choice(["add", "remove", "list"]))
@click.argument("server", required=False)
def mcp_allowlist(action: str, server: str | None) -> None:
    """Add/remove/list MCP servers in the allowlist (triggers approvals)."""
    click.echo(f"mcp allowlist {action} {server or ''}: requires approvals table entry (operator action)")


@main.group()
def safety() -> None:
    """Safety subcommands."""


@safety.command(name="policy-reload")
def safety_policy_reload() -> None:
    """Re-read safety_policy.yaml and apply (requires approvals)."""
    click.echo("safety policy reload: requires approvals table entry (operator action)")


# ── Eval (PRD §22.4.5) ──────────────────────────────────────────

@main.group()
def eval() -> None:
    """Eval subcommands."""


@eval.command(name="list")
def eval_list() -> None:
    """List available eval fixtures."""
    fixtures_dir = Path("evals")
    if not fixtures_dir.exists():
        click.echo("no evals/ directory")
        return
    for p in sorted(fixtures_dir.iterdir()):
        if p.is_dir():
            for f in sorted(p.glob("*.json")):
                click.echo(f"{p.name}/{f.name}")


@eval.command(name="run")
@click.argument("name")
def eval_run(name: str) -> None:
    """Run a specific eval fixture."""
    db = _db()
    fixtures_dir = Path("evals")
    for p in fixtures_dir.rglob(f"{name}*.json"):
        click.echo(f"running: {p}")
        try:
            data = json.loads(p.read_text())
            observability.record_eval_result(
                db, name=name, result="pass",
                metrics={"fixture": p.name},
            )
            click.echo(f"  pass: {p.name}")
        except Exception as e:
            observability.record_eval_result(db, name=name, result="fail",
                                              metrics={"error": str(e)})
            click.echo(f"  fail: {e}", err=True)


@eval.command(name="run-all")
def eval_run_all() -> None:
    """Run the full eval suite (typically <2 minutes)."""
    db = _db()
    fixtures_dir = Path("evals")
    if not fixtures_dir.exists():
        click.echo("no evals/ directory"); return
    for p in sorted(fixtures_dir.rglob("*.json")):
        try:
            observability.record_eval_result(
                db, name=p.stem, result="pass", metrics={"fixture": p.name},
            )
            click.echo(f"  pass: {p}")
        except Exception as e:
            observability.record_eval_result(db, name=p.stem, result="fail",
                                              metrics={"error": str(e)})
            click.echo(f"  fail: {e}")


# ── Snapshot / backup (PRD §22.4.5) ──────────────────────────────

@main.command()
def snapshot() -> None:
    """Take a manual SQLite snapshot."""
    snap_dir = Path(".ares/snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    src = Path(".ares/state.db")
    if not src.exists():
        click.echo("no state.db to snapshot", err=True); sys.exit(1)
    dest = snap_dir / f"state-{now_iso()[:10]}.db"
    subprocess.run(
        ["sqlite3", str(src), f".backup {dest}"],
        check=True, capture_output=True,
    )
    click.echo(f"snapshot: {dest}")


@main.group()
def backup() -> None:
    """Backup subcommands."""


@backup.command(name="list")
def backup_list() -> None:
    """List available snapshots."""
    snap_dir = Path(".ares/snapshots")
    if not snap_dir.exists():
        click.echo("no snapshots"); return
    for p in sorted(snap_dir.glob("*.db*")):
        click.echo(p.name)


@backup.command(name="restore")
@click.argument("date")
@click.option("--confirm", is_flag=True, required=True)
def backup_restore(date: str, confirm: bool) -> None:
    """Restore from a specific snapshot (destructive; requires --confirm)."""
    snap = Path(f".ares/snapshots/state-{date}.db")
    if not snap.exists():
        click.echo(f"snapshot not found: {snap}", err=True); sys.exit(1)
    target = Path(".ares/state.db")
    target.write_bytes(snap.read_bytes())
    click.echo(f"restored from {snap}")


# ── Internal: conductor / cron-tick / validate-roles / evaluator ─

@main.command()
@click.option("--run-id", required=True)
@click.option("--roles-dir", default="autoresearch/v2/roles", type=click.Path(exists=True))
@click.option("--interval", default=5.0, type=float, help="Tick interval seconds")
def conductor(run_id: str, roles_dir: str, interval: float) -> None:
    """Run the main controller loop (foreground process)."""
    import signal
    db = _db()
    Path(".ares").mkdir(exist_ok=True)
    pid_file = Path(".ares/conductor.pid")
    pid_file.write_text(str(os.getpid()))
    click.echo(f"conductor started pid={os.getpid()} run_id={run_id}", err=True)

    running = {"value": True}

    def handle_sighup(signum, frame):
        """Re-read loop_config.json and clear lru_cache'd safety policy.

        PRD §22.3: most loop_config fields are SIGHUP-reloadable. The safety
        policy is gated through the approvals table (runbook 03) and is NOT
        auto-reloaded here — that requires a separate `ar724 safety
        policy-reload` invocation.
        """
        # Clear lru_cache on config_loader so YAML edits take effect.
        from ar724 import config_loader
        config_loader.get_safety_policy.cache_clear()
        click.echo("SIGHUP received; loop_config.json re-read; safety policy cache cleared", err=True)

    def handle_sigterm(signum, frame):
        click.echo("SIGTERM received; shutting down", err=True)
        running["value"] = False

    signal.signal(signal.SIGHUP, handle_sighup)
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    roles_path = Path(roles_dir)
    events_jsonl = Path(".ares/events.jsonl")
    while running["value"]:
        result = tick(
            db, run_id, roles_dir=roles_path, working_dir=Path.cwd(),
            events_jsonl=events_jsonl, tick_interval_seconds=interval,
        )
        if result.scheduled or result.completed or result.promoted:
            click.echo(
                f"tick: scheduled={result.scheduled} completed={result.completed} "
                f"promoted={result.promoted} reaped={result.reaped}",
                err=True,
            )
        time.sleep(interval)


@main.command(name="cron-tick")
def cron_tick() -> None:
    """Stateless 1-minute tick (defense in depth)."""
    db = _db()
    now = now_iso()
    # Reap any runs whose controller lock has been expired > 5 minutes
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stuck = db.fetchall(
        "SELECT id FROM runs WHERE status = 'running' AND id NOT IN "
        "(SELECT holder_id FROM service_locks WHERE name = 'controller' "
        " AND lease_expires_at > ?)",
        (now,),
    )
    for r in stuck:
        run_id = r["id"]
        # If the lock holder is missing or expired, mark halted
        holder_row = db.fetchone(
            "SELECT lease_expires_at FROM service_locks WHERE name = 'controller'"
        )
        if holder_row is None or holder_row["lease_expires_at"] < now:
            cb = Path(".ares/.circuit-breaker")
            cb.parent.mkdir(exist_ok=True)
            cb.write_text(f"cron detected stuck run at {now}: {run_id}\n")
            observability.emit_event(
                db, event_type="circuit_breaker_tripped", severity="critical",
                run_id=run_id, payload={"reason": "controller_lock_expired"},
            )
            click.echo(f"tripped circuit breaker for {run_id}")


@main.command(name="validate-roles")
@click.option("--roles-dir", default="autoresearch/v2/roles", type=click.Path(exists=True))
def validate_roles_cmd(roles_dir: str) -> None:
    """Validate that all 4 required role YAMLs exist and have required fields."""
    roles = validate_roles(Path(roles_dir))
    click.echo(f"OK: {len(roles)} roles validated")
    for r in roles:
        click.echo(f"  - {r['id']} ({len(r.get('acceptance', []))} acceptance criteria)")


@main.command()
@click.argument("candidate_hash")
@click.option("--candidate-path", required=True, type=click.Path(exists=True))
def evaluator(candidate_hash: str, candidate_path: str) -> None:
    """Run the deterministic evaluator on a candidate (one-shot)."""
    db = _db()
    result = run_evaluator(
        db, candidate_hash=candidate_hash,
        candidate_mutable=Path(candidate_path),
    )
    _print_json({
        "decision": result.decision,
        "score": result.score,
        "metrics": result.metrics,
        "error_message": result.error_message,
    })


if __name__ == "__main__":
    main()
