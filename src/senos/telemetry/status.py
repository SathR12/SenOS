"""Read-only status service, independent of CLI rendering."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from senos.contracts import Observation, Workload, WorkloadState
from senos.workloads.errors import RegistrationError
from senos.workloads.registry import Registry
from . import fake


class StatusError(Exception):
    """A status snapshot could not be read."""


@dataclass(frozen=True)
class StatusRow:
    workload: Workload
    observation: Observation


@dataclass(frozen=True)
class StatusSnapshot:
    source: Literal["registry", "demo"]
    rows: tuple[StatusRow, ...]


def get_status(database: Path, *, demo: bool = False) -> StatusSnapshot:
    if demo:
        return StatusSnapshot("demo", tuple(StatusRow(workload, fake.observe(workload)) for workload in fake.list_workloads()))
    try:
        workloads = Registry(database).list_workloads()
    except (RegistrationError, ValidationError) as error:
        raise StatusError("Cannot read workload registry; check its path, permissions, and saved data") from error
    # Real collection is not implemented. Unknown state and None metrics avoid
    # making claims about a saved unit that may have stopped or restarted.
    return StatusSnapshot("registry", tuple(
        StatusRow(workload, Observation(workload_id=workload.workload_id, state=WorkloadState.UNKNOWN))
        for workload in workloads
    ))
