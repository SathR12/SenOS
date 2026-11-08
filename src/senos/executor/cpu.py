"""Restricted systemd CPUWeight reader/writer used by recovery."""

from dataclasses import dataclass
import re
import subprocess

from senos.contracts import Workload
from senos.recovery.errors import RestoreError
from senos.workloads.errors import RegistrationError
from senos.workloads.systemd import SystemdResolver


@dataclass(frozen=True)
class CPUState:
    weight: int | None
    invocation_id: str
    cgroup: str


class SystemdCPUBackend:
    def _run(self, workload: Workload, arguments: list[str]) -> str:
        try:
            result = subprocess.run(
                ["systemctl", f"--{workload.manager}", "--no-ask-password", *arguments],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RestoreError("Cannot contact systemd CPU controls") from error
        if result.returncode:
            raise RestoreError("Systemd refused CPUWeight operation; check manager, unit, and permissions")
        return result.stdout

    def read(self, workload: Workload) -> CPUState:
        try:
            resolved = SystemdResolver().resolve(workload.unit, workload.manager)
        except RegistrationError as error:
            raise RestoreError(str(error)) from error
        if resolved.unit != workload.unit:
            raise RestoreError("Unit identity changed; refusing recovery through a different alias")
        output = self._run(workload, ["show", "--no-pager", "--property=CPUWeight,InvocationID,ControlGroup", "--", workload.unit])
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        raw = values.get("CPUWeight", "")
        if raw == "[not set]":
            weight = None
        elif raw.isdecimal() and 1 <= int(raw) <= 10000:
            weight = int(raw)
        else:
            raise RestoreError("Unsupported or missing CPUWeight; no original value will be guessed")
        invocation = values.get("InvocationID", "")
        if not re.fullmatch(r"[0-9a-f]{32}", invocation) or invocation == "0" * 32:
            raise RestoreError("Cannot establish the unit invocation identity")
        if values.get("ControlGroup") != resolved.cgroup:
            raise RestoreError("Unit changed while reading CPU settings; retry")
        return CPUState(weight, invocation, resolved.cgroup)

    def write(self, workload: Workload, weight: int | None) -> None:
        # None means unset, not an invented default of 100.
        if weight is not None and (type(weight) is not int or not 1 <= weight <= 10000):
            raise RestoreError("Invalid recorded CPUWeight")
        value = "" if weight is None else str(weight)
        self._run(workload, ["set-property", "--runtime", "--", workload.unit, f"CPUWeight={value}"])
