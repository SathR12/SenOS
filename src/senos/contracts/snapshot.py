"""Contract for the immutable observations used by one policy decision."""

from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from ._base import ContractModel, CpuWeight, utc_now
from .observation import Observation


class ExecutionSnapshot(ContractModel):
    """Stable read-only evidence collected for an intent."""

    snapshot_id: UUID = Field(default_factory=uuid4)
    intent_id: UUID
    collected_at: AwareDatetime = Field(default_factory=utc_now)
    observations: tuple[Observation, ...]
    cpu_weights: dict[UUID, CpuWeight | None]
    invocation_ids: dict[UUID, str | None]
    cgroup_identities: dict[UUID, str | None]

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ExecutionSnapshot":
        workload_ids = {observation.workload_id for observation in self.observations}
        if set(self.cpu_weights) != workload_ids:
            raise ValueError("cpu_weights must contain exactly the observed workloads")
        if set(self.invocation_ids) != workload_ids:
            raise ValueError("invocation_ids must contain exactly the observed workloads")
        if set(self.cgroup_identities) != workload_ids:
            raise ValueError("cgroup_identities must contain exactly the observed workloads")
        if any(observation.intent_id != self.intent_id for observation in self.observations):
            raise ValueError("observations must belong to the snapshot intent")
        return self
