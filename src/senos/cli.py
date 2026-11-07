"""CLI entry point. Commands are placeholders until contracts and telemetry exist.

Keep this module limited to argument parsing and output so a future UI can
reuse the same application services.
"""

from importlib.metadata import version

import typer

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
) -> None:
    """Register a workload so SenOS may observe and manage it."""
    _not_implemented("register")


@app.command()
def restore(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be restored."),
) -> None:
    """Restore resource settings recorded before SenOS changed them."""
    _not_implemented("restore")
