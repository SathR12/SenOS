"""Read-only live telemetry from systemd and cgroup v2; never changes settings."""

from dataclasses import dataclass
from pathlib import Path
import math
import re
import time
from typing import Callable, Iterable

from senos.contracts import Observation, Workload, WorkloadState
from senos.workloads.errors import RegistrationError
from senos.workloads.systemd import is_dedicated_cgroup, read_unit_properties

CGROUP_ROOT = Path("/sys/fs/cgroup")
# CPU percent is a rate, so it needs two cpu.stat samples this far apart.
DEFAULT_INTERVAL_SECONDS = 0.5

_STATES = {
    "active": WorkloadState.ACTIVE,
    "inactive": WorkloadState.INACTIVE,
    "failed": WorkloadState.FAILED,
}


@dataclass(frozen=True)
class _Located:
    state: WorkloadState
    cgroup_dir: Path | None
    invocation_id: str | None = None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    try:
        value = int(text.strip()) if text is not None else None
        return value if value is not None and value >= 0 else None
    except ValueError:
        return None


def _cpu_usage_usec(cgroup_dir: Path) -> int | None:
    text = _read_text(cgroup_dir / "cpu.stat")
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "usage_usec":
            try:
                value = int(fields[1])
            except ValueError:
                return None
            return value if value >= 0 else None
    return None


def _pressure_avg10(path: Path, kind: str) -> float | None:
    """Parse the avg10 field from a PSI line such as 'some avg10=1.50 ...'."""
    text = _read_text(path)
    for line in (text or "").splitlines():
        fields = line.split()
        if fields and fields[0] == kind:
            for field in fields[1:]:
                key, _, value = field.partition("=")
                if key == "avg10":
                    try:
                        average = float(value)
                    except ValueError:
                        return None
                    return average if 0 <= average <= 100 else None
    return None


class LinuxTelemetry:
    """Observe registered units by re-resolving their current cgroup each time."""

    def __init__(
        self,
        cgroup_root: Path = CGROUP_ROOT,
        read_unit: Callable[[str, str], dict[str, str]] = read_unit_properties,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Telemetry interval must be finite and positive")
        self.cgroup_root = cgroup_root.resolve()
        self.read_unit = read_unit
        self.interval = interval
        self.clock = clock
        self.sleep = sleep

    def _locate(self, workload: Workload) -> _Located:
        try:
            properties = self.read_unit(workload.unit, workload.manager)
        except RegistrationError:
            return _Located(WorkloadState.UNKNOWN, None)
        state = _STATES.get(properties.get("ActiveState", ""), WorkloadState.UNKNOWN)
        cgroup = properties.get("ControlGroup", "")
        if state is not WorkloadState.ACTIVE or not is_dedicated_cgroup(cgroup):
            return _Located(state, None)
        if properties.get("LoadState") != "loaded" or properties.get("Id") != workload.unit:
            return _Located(WorkloadState.UNKNOWN, None)
        invocation = properties.get("InvocationID", "")
        if not re.fullmatch(r"[0-9a-f]{32}", invocation) or invocation == "0" * 32:
            return _Located(state, None)
        try:
            directory = (self.cgroup_root / cgroup.lstrip("/")).resolve()
        except (OSError, RuntimeError):
            return _Located(state, None)
        if not directory.is_relative_to(self.cgroup_root) or directory == self.cgroup_root:
            return _Located(state, None)
        return _Located(state, directory, invocation)

    def observe_all(self, workloads: Iterable[Workload]) -> list[Observation]:
        """Sample all workloads with one sleep and per-counter elapsed times."""
        workloads = list(workloads)
        before = []
        for workload in workloads:
            location = self._locate(workload)
            usage = _cpu_usage_usec(location.cgroup_dir) if location.cgroup_dir else None
            before.append((location, usage, self.clock()))
        if any(usage is not None for _, usage, _ in before):
            self.sleep(self.interval)

        observations = []
        for workload, (initial, start_usage, started) in zip(workloads, before):
            current = self._locate(workload)
            stable = (
                initial.cgroup_dir is not None and current.cgroup_dir == initial.cgroup_dir
                and current.invocation_id == initial.invocation_id
            )
            if not stable:
                observations.append(Observation(workload_id=workload.workload_id, state=current.state))
                continue
            directory = current.cgroup_dir
            end_usage = _cpu_usage_usec(directory)
            elapsed_usec = (self.clock() - started) * 1_000_000
            cpu_percent = None
            if start_usage is not None and end_usage is not None and end_usage >= start_usage and elapsed_usec > 0:
                cpu_percent = (end_usage - start_usage) / elapsed_usec * 100
            observations.append(Observation(
                workload_id=workload.workload_id,
                state=current.state,
                cpu_usage_percent=cpu_percent,
                memory_usage_bytes=_read_int(directory / "memory.current"),
                cpu_pressure_some_avg10=_pressure_avg10(directory / "cpu.pressure", "some"),
                memory_pressure_some_avg10=_pressure_avg10(directory / "memory.pressure", "some"),
                memory_pressure_full_avg10=_pressure_avg10(directory / "memory.pressure", "full"),
            ))
        return observations
