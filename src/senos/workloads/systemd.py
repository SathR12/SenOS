"""Read-only systemd unit resolution; no resource operations live here."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import subprocess
import sys

from senos.contracts.workload import validate_unit_name

from .errors import RegistrationError


def validate_unit(unit: str) -> None:
    try:
        validate_unit_name(unit)
    except ValueError as error:
        raise RegistrationError(str(error)) from error


@dataclass(frozen=True)
class UnitInfo:
    unit: str
    cgroup: str


def is_dedicated_cgroup(cgroup: str) -> bool:
    """Accept an absolute, non-root cgroup path without traversal."""
    path = PurePosixPath(cgroup)
    return cgroup.startswith("/") and not cgroup.startswith("//") and path != PurePosixPath("/") and ".." not in path.parts


def read_unit_properties(unit: str, manager: str) -> dict[str, str]:
    """Read Id, LoadState, ActiveState, and ControlGroup without changing the unit."""
    validate_unit(unit)
    if manager not in {"user", "system"}:
        raise RegistrationError("Manager must be user or system")
    if sys.platform != "linux":
        raise RegistrationError("Registration requires Linux with systemd; run SenOS inside WSL 2 or Linux")
    args = ["systemctl", f"--{manager}", "show", "--no-pager", "--property=Id,LoadState,ActiveState,ControlGroup", "--", unit]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except subprocess.TimeoutExpired as error:
        raise RegistrationError("Timed out reading systemd unit; check the selected manager") from error
    except OSError as error:
        raise RegistrationError("Cannot run systemctl; check that systemd is installed and running") from error
    if result.returncode:
        raise RegistrationError("Cannot read unit from systemd; check the name, manager, and session bus")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


class SystemdResolver:
    def resolve(self, unit: str, manager: str) -> UnitInfo:
        properties = read_unit_properties(unit, manager)
        if properties.get("LoadState") != "loaded":
            raise RegistrationError("Unit is not loaded; start the intended workload before registering it")
        if properties.get("ActiveState") != "active":
            raise RegistrationError("Unit is not active; start the intended workload before registering it")
        canonical = properties.get("Id", "")
        validate_unit(canonical)
        cgroup = properties.get("ControlGroup", "")
        if not is_dedicated_cgroup(cgroup):
            raise RegistrationError("Unit has no valid dedicated cgroup")
        return UnitInfo(unit=canonical, cgroup=cgroup)
