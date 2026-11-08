"""CLI entry point. Registration, live Linux status, and recorded CPU restoration.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from dataclasses import asdict
from importlib.metadata import version
import json
from pathlib import Path

import typer

from senos.config import default_registry_path
from senos.telemetry.status import StatusError, get_status
from senos.recovery.restore import RestoreError, RestoreService
from senos.workloads.registry import Registry, RegistrationError

app = typer.Typer(
    help="SenOS: natural-language user intent driving Linux resource policies.",
    no_args_is_help=True,
)


agent_app = typer.Typer(help="Evaluate proposal agents without applying resource policies.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")


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
        typer.echo(json.dumps({"created": created, "workload": asdict(workload)}))
    else:
        label = "Registered" if created else "Already registered"
        typer.echo(f"{label} {workload.name}: {workload.unit} ({workload.manager}, id={workload.id})")


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
