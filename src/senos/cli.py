"""CLI entry point. Registry, status, and intent work; restore is pending.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from importlib.metadata import version
import json
from pathlib import Path

import typer

from senos.config import default_registry_path
from senos.intent import IntentError, IntentService
from senos.telemetry.status import StatusError, get_status
from senos.workloads.registry import Registry, RegistrationError

app = typer.Typer(
    help="SenOS: natural-language user intent driving Linux resource policies.",
    no_args_is_help=True,
)
intent_app = typer.Typer(help="Submit validated workload objectives.")
app.add_typer(intent_app, name="intent")


def _not_implemented(command: str) -> None:
    typer.echo(f"senos {command}: not implemented yet.", err=True)
    raise typer.Exit(code=1)


@app.callback()
def main() -> None:
    """Submit objectives, inspect state, and restore policies."""


@app.command("version")
def show_version() -> None:
    """Print the installed SenOS version."""
    typer.echo(version("senos"))


@intent_app.command("prioritize")
def prioritize_intent(
    target: str = typer.Argument(..., help="Registered workload name to prioritize."),
    duration_seconds: int = typer.Option(..., "--duration-seconds", help="Intent duration from 1 through 86400 seconds."),
    preserve: list[str] | None = typer.Option(None, "--preserve", help="Registered workload that must keep progressing; repeat as needed."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print the validated intent as JSON."),
) -> None:
    """Validate a temporary priority objective; no resource policy is applied."""
    try:
        intent = IntentService(
            Registry(db if db is not None else default_registry_path())
        ).prioritize(target, duration_seconds, tuple(preserve or ()))
    except IntentError as error:
        typer.echo(f"senos intent: {error}", err=True)
        raise typer.Exit(code=1) from error

    if json_output:
        typer.echo(json.dumps({
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
    typer.echo("No resource policy was applied.")


@app.command()
def status(
    demo: bool = typer.Option(False, "--demo", help="Show labeled synthetic samples instead of registered workloads."),
    db: Path | None = typer.Option(None, "--db", envvar="SENOS_REGISTRY_PATH", help="Override the per-user registry database path."),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable status snapshot."),
) -> None:
    """List saved workloads; metrics are unavailable until real telemetry exists."""
    try:
        snapshot = get_status(db if db is not None else default_registry_path(), demo=demo)
    except StatusError as error:
        typer.echo(f"senos status: {error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps({
            "source": snapshot.source,
            "live_telemetry": False,
            "workloads": [
                {"workload": row.workload.model_dump(mode="json"), "observation": row.observation.model_dump(mode="json")}
                for row in snapshot.rows
            ],
        }))
        return
    if demo:
        typer.echo("DEMO: synthetic workloads and readings; not your registered applications.")
    else:
        typer.echo("Saved registrations only. Live telemetry is not implemented; unavailable readings are shown as N/A.")
    if not snapshot.rows:
        typer.echo("No workloads registered." if not demo else "No demo workloads available.")
        return
    typer.echo(f"{'NAME':<16} {'UNIT':<28} {'MANAGER':<8} {'STATE':<9} {'CPU %':>6} {'MEM MiB':>9}")
    for row in snapshot.rows:
        workload, observation = row.workload, row.observation
        cpu = "N/A" if observation.cpu_usage_percent is None else f"{observation.cpu_usage_percent:.1f}"
        memory = "N/A" if observation.memory_usage_bytes is None else f"{observation.memory_usage_bytes / 1024**2:.0f}"
        typer.echo(f"{workload.name:<16} {workload.unit:<28} {workload.manager:<8} {observation.state.value:<9} {cpu:>6} {memory:>9}")


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
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be restored."),
) -> None:
    """Restore resource settings recorded before SenOS changed them."""
    _not_implemented("restore")
