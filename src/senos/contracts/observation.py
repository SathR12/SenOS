"""Contract for observed workload health and resource use."""

from enum import Enum
from uuid import UUID

from pydantic import AwareDatetime, Field

from ._base import ContractModel, Percent, utc_now


class WorkloadState(str, Enum):
    """Lifecycle state reported by the workload telemetry adapter."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Observation(ContractModel):
    """A point-in-time set of health vitals for one workload."""

    intent_id: UUID | None = None
    workload_id: UUID
    observed_at: AwareDatetime = Field(default_factory=utc_now)
    state: WorkloadState
    cpu_usage_percent: float | None = Field(
        default=None, ge=0,
        description="CPU use where 100 percent represents one fully used logical CPU.",
    )
    memory_usage_bytes: int | None = Field(default=None, strict=True, ge=0, description="Workload cgroup memory.current in bytes; None means unavailable.")
    cpu_pressure_some_avg10: Percent | None = None
    memory_pressure_some_avg10: Percent | None = None
    memory_pressure_full_avg10: Percent | None = None
    cpu_weight: int | None = Field(default=None, strict=True, ge=1, le=10_000)
    invocation_id: str | None = None
    cgroup_identity: str | None = None
