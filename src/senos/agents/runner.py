"""CPU dry-run harness: synthetic, file, or read-only live inputs."""

from datetime import datetime, timezone
from pathlib import Path

from .cpu import CPUAgent, CPUContext
from .demo import cpu_context
from .live import collect_live_context


def run_cpu(*, input_path: Path | None = None, target_name: str | None = None, database: Path | None = None, preserve_names: tuple[str, ...] = (), duration_seconds: int = 600) -> dict:
    if target_name is None and (preserve_names or duration_seconds != 600):
        raise ValueError("Live intent options require a target workload")
    warnings = ()
    if target_name is not None:
        if database is None or input_path is not None:
            raise ValueError("Live input requires a registry path and cannot be combined with a file")
        context, warnings = collect_live_context(database, target_name, preserve_names=preserve_names, duration_seconds=duration_seconds)
        source = "live"
    elif input_path is not None:
        context = CPUContext.model_validate_json(input_path.read_text(encoding="utf-8"))
        source = "file"
    else:
        context = cpu_context()
        source = "demo"
    # Decide after collection, so newly collected timestamps are not in the future.
    now = datetime.now(timezone.utc)
    return {
        "mode": "dry_run", "source": source,
        "context": context.model_dump(mode="json"), "warnings": list(warnings),
        "decision": CPUAgent().evaluate(context, now=now).model_dump(mode="json"),
    }
