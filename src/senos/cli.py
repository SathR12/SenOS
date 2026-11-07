"""CLI entry point. `status` uses fake telemetry; other commands are placeholders.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from importlib.metadata import version

import typer

# Fake data until real telemetry runs on the Linux VM.
from senos.telemetry import fake

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
    workloads = fake.list_workloads()
    if not workloads:
        typer.echo("No workloads registered.")
        return

    typer.echo(f"{'NAME':<12} {'ROLE':<12} {'CPU %':>6} {'MEM MB':>8}")
    for workload in workloads:
        obs = fake.observe(workload)
        typer.echo(
            f"{workload.name:<12} {workload.role.value:<12} "
            f"{obs.cpu_percent:>6.1f} {obs.memory_mb:>8.0f}"
        )


@app.command()
def register(
    name: str = typer.Argument(..., help="Name used to refer to the workload."),
    unit: str = typer.Option(..., help="systemd unit that runs the workload."),
) -> None:
    """Register a workload so SenOS may observe and manage it."""
    _not_implemented("register")


@app.command()
def restore(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be restored."),
) -> None:
    """Restore resource settings recorded before SenOS changed them."""
    _not_implemented("restore")
