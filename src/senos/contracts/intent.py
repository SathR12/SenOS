"""Contract for a validated user objective."""

from enum import Enum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from ._base import ContractModel, utc_now


class IntentKind(str, Enum):
    """Objectives supported by the first deterministic policy milestone."""

    PRIORITIZE = "prioritize"
    RESTORE = "restore"


class Intent(ContractModel):
    """What the user wants SenOS to do with a managed workload."""

    intent_id: UUID = Field(default_factory=uuid4)
    kind: IntentKind
    target_workload_id: UUID
    preserve_workload_ids: tuple[UUID, ...] = ()
    duration_seconds: int | None = Field(default=None, ge=1, le=86_400)
    source_text: str = Field(min_length=1, max_length=1_000)
    submitted_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_objective(self) -> "Intent":
        """Enforce duration and workload relationships for each objective."""
        if len(set(self.preserve_workload_ids)) != len(self.preserve_workload_ids):
            raise ValueError("preserve_workload_ids must not contain duplicates")
        if self.target_workload_id in self.preserve_workload_ids:
            raise ValueError("the target workload cannot also be a preserved workload")
        if self.kind is IntentKind.PRIORITIZE and self.duration_seconds is None:
            raise ValueError("a prioritize intent requires duration_seconds")
        if self.kind is IntentKind.RESTORE and self.duration_seconds is not None:
            raise ValueError("a restore intent cannot have duration_seconds")
        return self
