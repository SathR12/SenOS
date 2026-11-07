"""CLI entry point. Registration works; status and restore remain placeholders.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from dataclasses import asdict
from importlib.metadata import version
import json
from pathlib import Path

import typer

from senos.config import default_registry_path
from senos.workloads.registry import Registry, RegistrationError

app = typer.Typer(
    help="SenOS: natural-language user intent driving Linux resource policies.",
    no_args_is_help=True,
)


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


@app.command()
def status() -> None:
    """Show registered workloads and current resource observations."""
    _not_implemented("status")


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
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be restored."),
) -> None:
    """Restore resource settings recorded before SenOS changed them."""
    _not_implemented("restore")
