"""Explicit demo data. Never reads or writes the user's workload registry."""

from senos.contracts import Observation, Workload, WorkloadState

_WORKLOADS = (
    Workload(workload_id="00000000-0000-4000-8000-000000000001", name="editor", unit="senos-editor.service"),
    Workload(workload_id="00000000-0000-4000-8000-000000000002", name="build", unit="senos-build.service"),
)
# Bytes use MiB (1024 squared), matching the CLI's explicitly labeled display.
_READINGS = {
    _WORKLOADS[0].workload_id: (12.0, 300 * 1024**2),
    _WORKLOADS[1].workload_id: (85.0, 1200 * 1024**2),
}


def list_workloads() -> list[Workload]:
    return list(_WORKLOADS)


def observe(workload: Workload) -> Observation:
    """Return synthetic readings only for a known demo identity."""
    try:
        cpu_percent, memory_bytes = _READINGS[workload.workload_id]
    except KeyError as error:
        raise ValueError("No demo reading exists for this workload") from error
    return Observation(
        workload_id=workload.workload_id,
        state=WorkloadState.ACTIVE,
        cpu_usage_percent=cpu_percent,
        memory_usage_bytes=memory_bytes,
    )
