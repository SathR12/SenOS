"""Read-only systemd unit resolution; no resource operations live here."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import subprocess
import sys

from .errors import RegistrationError


# Initial subset: simple units and instantiated templates, without escapes.
_UNIT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]*\.(?:service|scope)")


def validate_unit(unit: str) -> None:
    if len(unit) > 255 or not _UNIT.fullmatch(unit) or "@." in unit:
        raise RegistrationError("Use an explicit .service or .scope name, without paths, patterns, or shell syntax")


@dataclass(frozen=True)
class UnitInfo:
    unit: str
    cgroup: str


class SystemdResolver:
    def resolve(self, unit: str, manager: str) -> UnitInfo:
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
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if properties.get("LoadState") != "loaded":
            raise RegistrationError("Unit is not loaded; start the intended workload before registering it")
        if properties.get("ActiveState") != "active":
            raise RegistrationError("Unit is not active; start the intended workload before registering it")
        canonical = properties.get("Id", "")
        validate_unit(canonical)
        cgroup = properties.get("ControlGroup", "")
        path = PurePosixPath(cgroup)
        if not cgroup.startswith("/") or cgroup.startswith("//") or path == PurePosixPath("/") or ".." in path.parts:
            raise RegistrationError("Unit has no valid dedicated cgroup")
        return UnitInfo(unit=canonical, cgroup=cgroup)
