"""Small input/output harness; it has no executor or systemd dependency."""

from datetime import datetime, timezone
from pathlib import Path

from .cpu import CPUAgent, CPUContext
from .demo import cpu_context


def run_cpu(*, input_path: Path | None = None) -> dict:
    now = datetime.now(timezone.utc)
    context = cpu_context(now) if input_path is None else CPUContext.model_validate_json(input_path.read_text(encoding="utf-8"))
    return {
        "mode": "dry_run",
        "source": "demo" if input_path is None else "file",
        "decision": CPUAgent().evaluate(context, now=now).model_dump(mode="json"),
    }
