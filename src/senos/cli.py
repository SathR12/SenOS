"""CLI entry point. Registration, intent validation, live status, CPU proposals, and restoration.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from dataclasses import asdict
from importlib.metadata import version
import json
import math
from pathlib import Path

import typer

from senos.config import default_registry_path
from senos.intent import IntentError, IntentService
from senos.telemetry.status import StatusError, get_status
from senos.recovery.restore import RestoreError, RestoreService
from senos.workloads.registry import Registry, RegistrationError

app = typer.Typer(
    help="SenOS: natural-language user intent driving Linux resource policies.",
    no_args_is_help=True,
)
intent_app = typer.Typer(help="Submit validated workload objectives.")
app.add_typer(intent_app, name="intent")


agent_app = typer.Typer(help="Evaluate resource goals; CPU changes require explicit --watch --apply.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")


monitor_app = typer.Typer(help="Monitoring sessions and local history; dry-run by default.", no_args_is_help=True)
app.add_typer(monitor_app, name="monitor")


def _watch_intent(intent_payload, database, monitor_db, interval, max_cycles, json_output, *, initial=None, apply=False):
    from senos.agents.monitor import monitor_intent
    from senos.contracts import Intent
    from senos.storage.monitor import MonitorError

    history = monitor_db if monitor_db is not None else database.parent / "monitor.sqlite3"
    def started(run_id):
        if json_output:
            typer.echo(json.dumps({"event": "started", "run_id": run_id, "monitor_database": str(history.resolve()), "mode": "apply" if apply else "dry_run", "executed": False}))
        else:
            typer.echo(f"Monitor run {run_id}; history={history}. Ctrl+C stops observation.")
    def observed(event):
        if json_output:
            typer.echo(json.dumps({"event": "cycle", **event}))
            return
        evaluation = event["evaluation"]
        decision = evaluation["decision"]
        target = evaluation["intent"]["target_workload_id"]
        samples = [sample for sample in evaluation["context"]["observations"] if sample["workload_id"] == target]
        latest = samples[-1] if samples else {}
        cpu, pressure = latest.get("cpu_usage_percent"), latest.get("cpu_pressure_some_avg10")
        cpu_text = "N/A" if cpu is None else f"{cpu:.1f}%"
        psi_text = "N/A" if pressure is None else f"{pressure:.1f}%"
        label = "APPLY MODE" if apply else "DRY RUN"
        typer.echo(f"Cycle {event['cycle']}: {label} {decision['status']}; CPU={cpu_text}; PSI={psi_text}: {decision['reason']}")
        if evaluation.get("execution") is not None:
            action = evaluation["execution"]
            typer.echo(f"CPU action: {action['status']}; requested={action['requested_cpu_weight']}; effective={action['effective_cpu_weight']}: {action['reason']}")
        elif decision["proposal"] is not None:
            proposal = decision["proposal"]
            typer.echo(f"Would set CPUWeight {proposal['expected_cpu_weight']} -> {proposal['proposed_cpu_weight']} (not applied)")
    try:
        result = monitor_intent(Intent.model_validate(intent_payload), database, history,
            interval=interval, max_cycles=max_cycles, initial_evaluation=initial, apply=apply,
            on_event=observed, on_start=started)
    except (MonitorError, ValueError) as error:
        typer.echo(f"senos monitor: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps({"event": "finished", "result": result}))
    else:
        if apply:
            typer.echo(f"Monitoring {result['status']} after {result['cycles']} cycles. Original CPU settings restored and verified.")
        else:
            typer.echo(f"Monitoring {result['status']} after {result['cycles']} cycles. No resource policy was applied.")


@monitor_app.command("start")
def monitor_start(
    apply: bool = typer.Option(False, "--apply", help="Apply validated CPU boosts and restore on exit; user units only."),
    target: str = typer.Argument(..., help="Registered workload to observe."),
    duration_seconds: int = typer.Option(..., "--duration-seconds", min=1, max=86400),
    preserve: list[str] = typer.Option([], "--preserve"),
    interval: float = typer.Option(5, "--interval", min=1, max=60),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, max=10000),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH"),
    monitor_db: Path | None = typer.Option(None, "--monitor-db"),
    json_output: bool = typer.Option(False, "--json", help="Stream JSON Lines events."),
) -> None:
    """Monitor a structured intent without an LLM; --apply enables bounded CPU changes."""
    database = db if db is not None else default_registry_path()
    try:
        intent = IntentService(Registry(database)).prioritize(target, duration_seconds, tuple(preserve))
    except IntentError as error:
        typer.echo(f"senos monitor: {error}", err=True)
        raise typer.Exit(code=1) from error
    _watch_intent(intent.model_dump(mode="json"), database, monitor_db, interval, max_cycles, json_output, apply=apply)


def _monitor_store(path):
    from senos.storage.monitor import MonitorStore
    return MonitorStore(path if path is not None else default_registry_path().parent / "monitor.sqlite3")


@monitor_app.command("list")
def monitor_list(monitor_db: Path | None = typer.Option(None, "--monitor-db")) -> None:
    """List saved monitoring sessions as JSON; does not start monitoring."""
    from senos.storage.monitor import MonitorError
    try:
        typer.echo(json.dumps({"sessions": _monitor_store(monitor_db).list_runs()}))
    except MonitorError as error:
        typer.echo(f"senos monitor: {error}", err=True)
        raise typer.Exit(code=1) from error


@monitor_app.command("show")
def monitor_show(
    run_id: str = typer.Argument(...), monitor_db: Path | None = typer.Option(None, "--monitor-db"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    """Read saved session details and the latest bounded set of events as JSON."""
    from senos.storage.monitor import MonitorError
    store = _monitor_store(monitor_db)
    try:
        typer.echo(json.dumps({"session": store.get(run_id), "events": store.events(run_id, limit)}))
    except MonitorError as error:
        typer.echo(f"senos monitor: {error}", err=True)
        raise typer.Exit(code=1) from error


@monitor_app.command("stop")
def monitor_stop(run_id: str = typer.Argument(...), monitor_db: Path | None = typer.Option(None, "--monitor-db")) -> None:
    """Stop an observation session from another terminal; never modify its workloads."""
    from senos.storage.monitor import MonitorError
    try:
        typer.echo(json.dumps(_monitor_store(monitor_db).stop(run_id)))
    except MonitorError as error:
        typer.echo(f"senos monitor: {error}", err=True)
        raise typer.Exit(code=1) from error


@agent_app.command("run")
def llm_agent(
    request: str = typer.Argument(..., help="Natural-language objective for registered Linux workloads."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Registry database."),
    apply: bool = typer.Option(False, "--apply", help="Authorize bounded CPU application; requires --watch and restores on exit."),
    watch: bool = typer.Option(False, "--watch", help="Monitor the interpreted objective locally until its original deadline."),
    interval: float = typer.Option(5, "--interval", min=1, max=60, help="Pause between monitoring cycles, in seconds."),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, max=10000, help="Stop monitoring after this many cycles."),
    monitor_db: Path | None = typer.Option(None, "--monitor-db", help="Separate SQLite monitor history database."),
    provider_name: str | None = typer.Option(None, "--provider", help="gemini or openai; defaults to local configuration or available key."),
    model: str | None = typer.Option(None, "--model", help="Override the selected provider model."),
    json_output: bool = typer.Option(False, "--json", help="Print the tool trace and validated evaluation as JSON."),
) -> None:
    """Inspect with one LLM agent; explicit --watch --apply enables supervised CPU changes."""
    from senos.agents.llm import run_agent
    from senos.agents.provider import AgentError

    if apply and not watch:
        raise typer.BadParameter("--apply requires --watch so restoration is supervised")
    if not watch and (interval != 5 or max_cycles is not None or monitor_db is not None):
        raise typer.BadParameter("--interval, --max-cycles, and --monitor-db require --watch")
    if not math.isfinite(interval):
        raise typer.BadParameter("--interval must be finite")
    database = db if db is not None else default_registry_path()
    try:
        result = run_agent(request, database, model=model, provider_name=provider_name)
    except AgentError as error:
        typer.echo(f"senos agent: {error}", err=True)
        raise typer.Exit(code=1) from error
    if watch and result["status"] == "evaluated":
        if json_output:
            typer.echo(json.dumps({"event": "agent", "result": result}))
        else:
            typer.echo(f"LLM objective resolved once ({result['agent']['model']}); remaining cycles use local code.")
            typer.echo(f"Agent rationale: {result['rationale']}")
        _watch_intent(result["evaluation"]["intent"], database, monitor_db, interval, max_cycles, json_output, initial=result["evaluation"], apply=apply)
        return
    if json_output:
        typer.echo(json.dumps(result))
        return
    typer.echo(f"LLM agent ({result['agent']['model']}): {result['status']}")
    if result["status"] == "clarification":
        typer.echo(result["question"])
    elif result["status"] == "unsupported":
        typer.echo(result["reason"])
    else:
        typer.echo(f"Agent rationale: {result['rationale']}")
        evaluation = result["evaluation"]
        intent, decision = evaluation["intent"], evaluation["decision"]
        typer.echo(f"Validated intent: target={intent['target_workload_id']}; duration={intent['duration_seconds']} seconds")
        for warning in evaluation["warnings"]:
            typer.echo(f"Note: {warning}")
        typer.echo(f"DRY RUN: {decision['status']}: {decision['reason']}")
        if decision["proposal"] is not None:
            proposal = decision["proposal"]
            typer.echo(f"Would set CPUWeight {proposal['expected_cpu_weight']} -> {proposal['proposed_cpu_weight']}; expires {proposal['expires_at']}")
    typer.echo("No resource policy was applied.")


@agent_app.command("cpu")
def cpu_agent(
    demo: bool = typer.Option(False, "--demo", help="Evaluate a synthetic CPU-contention scenario."),
    input_path: Path | None = typer.Option(None, "--input", help="Read a CPUContext JSON file instead of the demo."),
    target: str | None = typer.Option(None, "--target", help="Collect live inputs for this registered workload name."),
    preserve: list[str] = typer.Option([], "--preserve", help="Registered workload to preserve; repeat for multiple names (live mode)."),
    duration: int = typer.Option(600, "--duration", min=1, max=86400, help="Intent duration in seconds (live mode)."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Registry database for live inputs."),
    json_output: bool = typer.Option(False, "--json", help="Print the full validated decision and proposal as JSON."),
) -> None:
    """Propose a bounded CPU-weight change; never execute it."""
    from senos.agents.runner import run_cpu

    if sum((demo, input_path is not None, target is not None)) != 1:
        raise typer.BadParameter("Choose exactly one of --demo, --input PATH, or --target NAME")
    if target is None and (preserve or duration != 600):
        raise typer.BadParameter("--preserve and --duration require --target")
    if target is not None and not json_output:
        typer.echo("Reading live inputs (three rounds; usually a few seconds)...", err=True)
    try:
        result = run_cpu(input_path=input_path, target_name=target, database=db if db is not None else default_registry_path(), preserve_names=tuple(preserve), duration_seconds=duration)
    except (OSError, ValueError) as error:
        typer.echo(f"Invalid CPU context: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps(result))
        return
    if result["source"] == "live":
        context = result["context"]
        target_id = context["intent"]["target_workload_id"]
        samples = [sample for sample in context["observations"] if sample["workload_id"] == target_id]
        latest = samples[-1] if samples else {}
        cpu = latest.get("cpu_usage_percent")
        pressure = latest.get("cpu_pressure_some_avg10")
        weight = context["current_cpu_weights"].get(target_id)
        cpu_text = "N/A" if cpu is None else f"{cpu:.1f}%"
        pressure_text = "N/A" if pressure is None else f"{pressure:.1f}%"
        typer.echo(f"Live target={target}; samples={len(samples)}; weight={weight if weight is not None else 'N/A'}; CPU={cpu_text}; PSI={pressure_text}")
        for warning in result["warnings"]:
            typer.echo(f"Note: {warning}")
    decision = result["decision"]
    typer.echo(f"DRY RUN ({result['source']}): {decision['status']}: {decision['reason']}")
    if decision["proposal"] is not None:
        proposal = decision["proposal"]
        typer.echo(f"Would set CPUWeight {proposal['expected_cpu_weight']} -> {proposal['proposed_cpu_weight']} for {proposal['workload_id']}; expires {proposal['expires_at']}")


@app.callback()
def main() -> None:
    """Submit objectives, inspect state, and restore policies."""


@app.command("version")
def show_version() -> None:
    """Print the installed SenOS version."""
    typer.echo(version("senos"))


@intent_app.command("prioritize")
def prioritize_intent(
    evaluate: bool = typer.Option(False, "--evaluate", help="Collect live telemetry and evaluate a CPU proposal without applying it."),
    target: str = typer.Argument(..., help="Registered workload name to prioritize."),
    duration_seconds: int = typer.Option(..., "--duration-seconds", help="Intent duration from 1 through 86400 seconds."),
    preserve: list[str] | None = typer.Option(None, "--preserve", help="Registered workload that must keep progressing; repeat as needed."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print the validated intent as JSON."),
) -> None:
    """Validate a temporary priority objective; no resource policy is applied."""
    database = db if db is not None else default_registry_path()
    evaluation = None
    try:
        intent = IntentService(
            Registry(database)
        ).prioritize(target, duration_seconds, tuple(preserve or ()))
        if evaluate:
            from senos.intent.pipeline import evaluate_intent

            if not json_output:
                typer.echo("Reading live inputs for the validated intent...", err=True)
            evaluation = evaluate_intent(intent, database)
    except IntentError as error:
        typer.echo(f"senos intent: {error}", err=True)
        raise typer.Exit(code=1) from error

    if json_output:
        typer.echo(json.dumps(evaluation if evaluation is not None else {
            "accepted": True,
            "executed": False,
            "intent": intent.model_dump(mode="json"),
        }))
        return

    typer.echo(
        f"Validated intent: prioritize {target.strip()} for {duration_seconds} seconds."
    )
    if preserve:
        typer.echo(f"Preserve: {', '.join(name.strip() for name in preserve)}")
    if evaluation is not None:
        decision = evaluation["decision"]
        for warning in evaluation["warnings"]:
            typer.echo(f"Note: {warning}")
        typer.echo(f"DRY RUN (live): {decision['status']}: {decision['reason']}")
        if decision["proposal"] is not None:
            proposal = decision["proposal"]
            typer.echo(f"Would set CPUWeight {proposal['expected_cpu_weight']} -> {proposal['proposed_cpu_weight']}; expires {proposal['expires_at']}")
    typer.echo("No resource policy was applied.")


@app.command()
def status(
    demo: bool = typer.Option(False, "--demo", help="Show labeled synthetic samples instead of registered workloads."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable status snapshot."),
) -> None:
    """List saved workloads with live CPU, memory, and pressure readings on Linux."""
    try:
        snapshot = get_status(db if db is not None else default_registry_path(), demo=demo)
    except StatusError as error:
        typer.echo(f"senos status: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps({
            "source": snapshot.source,
            "live_telemetry": snapshot.live,
            "workloads": [
                {"workload": row.workload.model_dump(mode="json"), "observation": row.observation.model_dump(mode="json")}
                for row in snapshot.rows
            ],
        }))
        return
    if demo:
        typer.echo("DEMO: synthetic workloads and readings; not your registered applications.")
    elif snapshot.live:
        typer.echo("Live readings from systemd and cgroup v2; CPU is averaged over a short sample. Unavailable readings are shown as N/A.")
    else:
        typer.echo("Saved registrations only. Live telemetry requires Linux with systemd and cgroup v2; unavailable readings are shown as N/A.")
    if not snapshot.rows:
        typer.echo("No workloads registered." if not demo else "No demo workloads available.")
        return
    typer.echo(f"{'NAME':<16} {'UNIT':<28} {'MANAGER':<8} {'STATE':<9} {'CPU %':>6} {'MEM MiB':>9} {'CPU PSI':>8}")
    for row in snapshot.rows:
        workload, observation = row.workload, row.observation
        cpu = "N/A" if observation.cpu_usage_percent is None else f"{observation.cpu_usage_percent:.1f}"
        memory = "N/A" if observation.memory_usage_bytes is None else f"{observation.memory_usage_bytes / 1024**2:.1f}"
        pressure = "N/A" if observation.cpu_pressure_some_avg10 is None else f"{observation.cpu_pressure_some_avg10:.1f}"
        typer.echo(f"{workload.name:<16} {workload.unit:<28} {workload.manager:<8} {observation.state.value:<9} {cpu:>6} {memory:>9} {pressure:>8}")


@app.command()
def register(
    name: str = typer.Argument(..., help="Name used to refer to the workload."),
    unit: str = typer.Option(..., help="systemd unit that runs the workload."),
    system: bool = typer.Option(False, "--system", help="Resolve a system unit instead of a user unit."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable registration record."),
) -> None:
    """Record an active workload selected by the user; no settings are changed."""
    try:
        workload, created = Registry(db if db is not None else default_registry_path()).register(name, unit, "system" if system else "user")
    except RegistrationError as error:
        typer.echo(f"senos register: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        payload = workload.model_dump(mode="json")
        payload["id"] = payload.pop("workload_id")
        typer.echo(json.dumps({"created": created, "workload": payload}))
    else:
        label = "Registered" if created else "Already registered"
        typer.echo(f"{label} {workload.name}: {workload.unit} ({workload.manager}, id={workload.workload_id})")


@app.command()
def unregister(
    name: str | None = typer.Argument(None, help="Registered workload name to remove."),
    all_workloads: bool = typer.Option(False, "--all", help="Remove every workload registration."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when using --all."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print the number of removed registrations as JSON."),
) -> None:
    """Remove registrations while leaving applications running."""
    if (name is not None and all_workloads) or (name is None and not all_workloads):
        raise typer.BadParameter("Provide a workload name or --all, not both")
    if all_workloads and not yes:
        typer.confirm("Remove all workload registrations? Applications will keep running", abort=True, err=True)
    try:
        count = Registry(db if db is not None else default_registry_path()).unregister(name, all_workloads=all_workloads)
    except RegistrationError as error:
        typer.echo(f"senos unregister: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps({"removed": count}))
    elif all_workloads:
        typer.echo(f"Unregistered {count} workloads. Applications keep running.")
    else:
        typer.echo(f"Unregistered {name.strip()}. Application keeps running.")


@app.command()
def restore(
    name: str | None = typer.Argument(None, help="Restore one registered workload; omit to restore all recorded changes."),
    all_workloads: bool = typer.Option(False, "--all", help="Restore all recorded workload changes."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check targets and show originals without changing settings or the journal."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print per-workload restore results as JSON."),
) -> None:
    """Restore recorded CPU weights, retaining registrations and failed recovery data."""
    if name is not None and all_workloads:
        raise typer.BadParameter("Provide a workload name or --all, not both")
    try:
        results = RestoreService(db if db is not None else default_registry_path()).restore(name, dry_run=dry_run)
    except RestoreError as error:
        typer.echo(f"senos restore: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps({"results": [asdict(result) for result in results]}))
    elif not results:
        typer.echo("No recorded changes to restore.")
    else:
        for result in results:
            original = "unset" if result.original_cpu_weight is None else str(result.original_cpu_weight)
            typer.echo(f"{result.name}: {result.status} CPUWeight={original}: {result.message}")
    if any(result.status == "failed" for result in results):
        raise typer.Exit(code=1)
