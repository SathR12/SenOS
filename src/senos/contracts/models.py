"""Minimal Workload and Observation models for `senos status`.

These are a starting point; extend them once the team agrees on the full contracts.
"""

from enum import Enum

from pydantic import BaseModel, Field


class WorkloadRole(str, Enum):
    INTERACTIVE = "interactive"  # user notices lag (editor, game, browser)
    BACKGROUND = "background"  # only needs to finish eventually (build, training)


class Workload(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    unit: str = Field(pattern=r"^[A-Za-z0-9@._-]+\.(service|scope)$")
    role: WorkloadRole


class Observation(BaseModel):
    workload_name: str
    cpu_percent: float = Field(ge=0, le=100)
    memory_mb: float = Field(ge=0)
