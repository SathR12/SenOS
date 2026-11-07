"""Contract for a bounded policy change proposed by SenOS."""

from enum import Enum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from ._base import ContractModel, CpuWeight, utc_now


class ProposalAction(str, Enum):
    """Allowlisted policy operations supported by the first milestone."""

    SET_CPU_WEIGHT = "set_cpu_weight"


class Proposal(ContractModel):
    """A requested CPU-weight transition awaiting deterministic validation."""

    proposal_id: UUID = Field(default_factory=uuid4)
    intent_id: UUID
    workload_id: UUID
    action: ProposalAction = ProposalAction.SET_CPU_WEIGHT
    expected_cpu_weight: CpuWeight
    proposed_cpu_weight: CpuWeight
    rationale: str = Field(min_length=1, max_length=1_000)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_change(self) -> "Proposal":
        """Require a real change with a future expiration boundary."""
        if self.expected_cpu_weight == self.proposed_cpu_weight:
            raise ValueError("proposed_cpu_weight must differ from expected_cpu_weight")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self
