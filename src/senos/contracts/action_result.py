"""Contract for the executor's observed result of a proposal."""

from enum import Enum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from ._base import ContractModel, CpuWeight, utc_now


class ActionStatus(str, Enum):
    """Possible terminal outcomes from the restricted executor."""

    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionResult(ContractModel):
    """What actually happened when the executor handled a proposal."""

    result_id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    workload_id: UUID
    status: ActionStatus
    requested_cpu_weight: CpuWeight
    previous_cpu_weight: CpuWeight | None = None
    effective_cpu_weight: CpuWeight | None = None
    reason: str = Field(min_length=1, max_length=1_000)
    started_at: AwareDatetime
    finished_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ActionResult":
        """Keep reported values consistent with the executor outcome."""
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")

        if self.status is ActionStatus.APPLIED:
            if self.effective_cpu_weight is None:
                if self.previous_cpu_weight is None:
                    raise ValueError(
                        "an applied result requires previous_cpu_weight and effective_cpu_weight"
                    )
                raise ValueError("an applied result requires effective_cpu_weight")
            if self.effective_cpu_weight != self.requested_cpu_weight:
                raise ValueError(
                    "effective_cpu_weight must equal requested_cpu_weight when applied"
                )
        elif self.status is ActionStatus.REJECTED:
            if self.effective_cpu_weight is not None:
                raise ValueError("a rejected result cannot report an effective CPU weight")

        return self
