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

    workload_id: UUID
    observed_at: AwareDatetime = Field(default_factory=utc_now)
    state: WorkloadState
    cpu_usage_percent: float = Field(
        ge=0,
        description="CPU use where 100 percent represents one fully used logical CPU.",
    )
    memory_usage_bytes: int = Field(ge=0)
    cpu_pressure_some_avg10: Percent | None = None
    memory_pressure_some_avg10: Percent | None = None
    memory_pressure_full_avg10: Percent | None = None
