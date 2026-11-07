"""Fake telemetry for developing on machines without cgroup v2 (e.g. macOS).

Real telemetry should provide the same functions, reading Linux instead of
returning fixed numbers.
"""

from senos.contracts.models import Observation, Workload, WorkloadRole

_WORKLOADS = [
    Workload(name="editor", unit="senos-editor.service", role=WorkloadRole.INTERACTIVE),
    Workload(name="build", unit="senos-build.service", role=WorkloadRole.BACKGROUND),
]

_READINGS = {"editor": (12.0, 300.0), "build": (85.0, 1200.0)}


def list_workloads() -> list[Workload]:
    return list(_WORKLOADS)


def observe(workload: Workload) -> Observation:
    cpu_percent, memory_mb = _READINGS[workload.name]
    return Observation(workload_name=workload.name, cpu_percent=cpu_percent, memory_mb=memory_mb)
