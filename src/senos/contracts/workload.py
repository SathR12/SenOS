"""Contract and shared syntax validation for registered workloads."""

import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator

from ._base import ContractModel, utc_now

# Initial subset: simple units and instantiated templates, without escapes.
_UNIT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]*\.(?:service|scope)")


def validate_unit_name(unit: str) -> str:
    """Validate syntax only; existence and permissions require systemd checks."""
    if len(unit) > 255 or not _UNIT.fullmatch(unit) or "@." in unit or unit.count("@") > 1:
        raise ValueError("Use an explicit .service or .scope name, without paths, patterns, or shell syntax")
    return unit


def validate_workload_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 128 or any(not character.isprintable() for character in name):
        raise ValueError("Workload name must contain 1 to 128 printable characters")
    return name


class Workload(ContractModel):
    """A registered unit; identity includes the user/system manager context."""

    workload_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=255)
    manager: Literal["user", "system"] = "user"
    registered_at: AwareDatetime = Field(default_factory=utc_now)

    @field_validator("unit")
    @classmethod
    def valid_unit(cls, value: str) -> str:
        return validate_unit_name(value)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return validate_workload_name(value)
