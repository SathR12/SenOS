"""Contract for a workload the user has allowed SenOS to manage."""

from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from ._base import ContractModel, utc_now


class Workload(ContractModel):
    """A registered systemd service inside the SenOS management boundary."""

    workload_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(
        min_length=9,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@\\-]*\.service$",
        description="Validated systemd service unit name, without a path.",
    )
    registered_at: AwareDatetime = Field(default_factory=utc_now)
