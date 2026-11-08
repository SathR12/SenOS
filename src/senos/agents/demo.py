"""Synthetic contention scenario for the CPU dry-run runner."""

from datetime import datetime, timedelta, timezone

from senos.contracts import Intent, Observation, Workload
from .cpu import CPUContext


def cpu_context(now: datetime | None = None) -> CPUContext:
    now = now if now is not None else datetime.now(timezone.utc)
    target = Workload(workload_id="00000000-0000-4000-8000-000000000001", name="web", unit="demo-web.service")
    background = Workload(workload_id="00000000-0000-4000-8000-000000000002", name="build", unit="demo-build.service")
    observations = [Observation(workload_id=target.workload_id, observed_at=now - timedelta(seconds=age), state="active", cpu_usage_percent=70, cpu_pressure_some_avg10=15) for age in (4, 2, 0)]
    observations.append(Observation(workload_id=background.workload_id, observed_at=now, state="active", cpu_usage_percent=150))
    return CPUContext(
        intent=Intent(kind="prioritize", target_workload_id=target.workload_id, preserve_workload_ids=(background.workload_id,), duration_seconds=600, source_text="Prioritize web while the build continues", submitted_at=now - timedelta(seconds=5)),
        workloads=(target, background), observations=tuple(observations),
        current_cpu_weights={target.workload_id: 100, background.workload_id: 100},
    )
