"""Common behavior for data passed between SenOS components."""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return an aware UTC timestamp for contract defaults."""
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    """Immutable, closed-schema base for cross-component contracts."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


CpuWeight = Annotated[int, Field(ge=1, le=10_000)]
Percent = Annotated[float, Field(ge=0, le=100)]
