import json

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.contracts import Workload, WorkloadState
from senos.telemetry import status as status_service
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.errors import RegistrationError
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo

CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/job.service"


class Clock:
    """Advances by exactly the requested sleep so CPU percent is deterministic."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_cgroup(root, usage_usec=1_000_000, memory=50 * 1024**2):
    directory = root / CGROUP.lstrip("/")
    directory.mkdir(parents=True)
    (directory / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 0\nsystem_usec 0\n")
    (directory / "memory.current").write_text(f"{memory}\n")
    (directory / "cpu.pressure").write_text(
        "some avg10=12.50 avg60=3.00 avg300=1.00 total=10\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    (directory / "memory.pressure").write_text(
        "some avg10=2.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=1.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    return directory


def unit_reader(active_state="active", cgroup=CGROUP):
    def read(unit, manager):
        return {"Id": unit, "LoadState": "loaded", "ActiveState": active_state, "ControlGroup": cgroup, "InvocationID": "a" * 32}
    return read


def telemetry(root, read_unit, clock, on_sleep=None):
    def sleep(seconds):
        clock.sleep(seconds)
        if on_sleep:
            on_sleep()
    return LinuxTelemetry(cgroup_root=root, read_unit=read_unit, interval=0.5, clock=clock, sleep=sleep)


def test_active_workload_reports_cpu_memory_and_pressure(tmp_path):
    directory = make_cgroup(tmp_path)
    clock = Clock()
    # 250 ms of CPU time during a 500 ms sample is half of one logical CPU.
    advance = lambda: (directory / "cpu.stat").write_text("usage_usec 1250000\n")
    workload = Workload(name="job", unit="job.service")

    [observation] = telemetry(tmp_path, unit_reader(), clock, advance).observe_all([workload])

    assert observation.workload_id == workload.workload_id
    assert observation.state is WorkloadState.ACTIVE
    assert observation.cpu_usage_percent == pytest.approx(50.0)
    assert observation.memory_usage_bytes == 50 * 1024**2
    assert observation.cpu_pressure_some_avg10 == 12.5
    assert observation.memory_pressure_some_avg10 == 2.0
    assert observation.memory_pressure_full_avg10 == 1.0


def test_multicore_usage_can_exceed_one_hundred_percent(tmp_path):
    directory = make_cgroup(tmp_path)
    clock = Clock()
    advance = lambda: (directory / "cpu.stat").write_text("usage_usec 2000000\n")

    [observation] = telemetry(tmp_path, unit_reader(), clock, advance).observe_all([Workload(name="job", unit="job.service")])

    assert observation.cpu_usage_percent == pytest.approx(200.0)


@pytest.mark.parametrize("active_state, expected", [
    ("inactive", WorkloadState.INACTIVE),
    ("failed", WorkloadState.FAILED),
    ("activating", WorkloadState.UNKNOWN),
])
def test_non_active_units_report_state_without_metrics(tmp_path, active_state, expected):
    make_cgroup(tmp_path)
    clock = Clock()

    [observation] = telemetry(tmp_path, unit_reader(active_state), clock).observe_all([Workload(name="job", unit="job.service")])

    assert observation.state is expected
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None
    assert clock.now == 100.0, "no sample interval is needed without an active cgroup"


def test_systemd_read_failure_is_unknown(tmp_path):
    def fail(unit, manager):
        raise RegistrationError("Cannot read unit from systemd")

    [observation] = telemetry(tmp_path, fail, Clock()).observe_all([Workload(name="job", unit="job.service")])

    assert observation.state is WorkloadState.UNKNOWN
    assert observation.cpu_usage_percent is None


@pytest.mark.parametrize("cgroup", ["", "/", "relative/job.service", "/user.slice/../../etc"])
def test_invalid_cgroup_paths_are_never_read(tmp_path, cgroup):
    [observation] = telemetry(tmp_path, unit_reader(cgroup=cgroup), Clock()).observe_all([Workload(name="job", unit="job.service")])

    assert observation.state is WorkloadState.ACTIVE
    assert observation.memory_usage_bytes is None


def test_missing_files_become_unavailable_readings(tmp_path):
    (tmp_path / CGROUP.lstrip("/")).mkdir(parents=True)

    [observation] = telemetry(tmp_path, unit_reader(), Clock()).observe_all([Workload(name="job", unit="job.service")])

    assert observation.state is WorkloadState.ACTIVE
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None
    assert observation.cpu_pressure_some_avg10 is None


def test_counter_reset_during_sample_is_unavailable(tmp_path):
    directory = make_cgroup(tmp_path, usage_usec=5_000_000)
    restart = lambda: (directory / "cpu.stat").write_text("usage_usec 10\n")

    [observation] = telemetry(tmp_path, unit_reader(), Clock(), restart).observe_all([Workload(name="job", unit="job.service")])

    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes == 50 * 1024**2


def test_status_cli_renders_live_readings(tmp_path, monkeypatch):
    database = tmp_path / "registry.sqlite3"

    class Resolver:
        def resolve(self, unit, manager):
            return UnitInfo(unit=unit, cgroup=CGROUP)

    Registry(database, Resolver()).register("job", "job.service")
    directory = make_cgroup(tmp_path / "cgroup")
    clock = Clock()
    advance = lambda: (directory / "cpu.stat").write_text("usage_usec 1250000\n")
    live = telemetry(tmp_path / "cgroup", unit_reader(), clock, advance)
    monkeypatch.setattr(status_service, "default_telemetry", lambda: live)

    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 0, result.output
    assert "Live readings" in result.output
    assert "active" in result.output and "50.0" in result.output and "12.5" in result.output

    clock.now = 100.0
    (directory / "cpu.stat").write_text("usage_usec 1000000\n")
    payload = json.loads(CliRunner().invoke(app, ["status", "--json", "--db", str(database)]).output)
    assert payload["live_telemetry"] is True
    assert payload["workloads"][0]["observation"]["memory_usage_bytes"] == 50 * 1024**2


def test_restart_with_increasing_counter_does_not_mix_invocations(tmp_path):
    directory = make_cgroup(tmp_path)
    properties = unit_reader()("job.service", "user")
    def restart():
        properties["InvocationID"] = "b" * 32
        (directory / "cpu.stat").write_text("usage_usec 20000000\n")
    reader = lambda unit, manager: dict(properties)
    [observation] = telemetry(tmp_path, reader, Clock(), restart).observe_all([Workload(name="job", unit="job.service")])
    assert observation.state is WorkloadState.ACTIVE
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None


def test_stop_during_sampling_reports_final_state(tmp_path):
    make_cgroup(tmp_path)
    properties = unit_reader()("job.service", "user")
    def stop():
        properties.update(ActiveState="inactive", ControlGroup="")
    [observation] = telemetry(tmp_path, lambda *args: dict(properties), Clock(), stop).observe_all([Workload(name="job", unit="job.service")])
    assert observation.state is WorkloadState.INACTIVE
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None


@pytest.mark.parametrize("content", [b"-1", b"not-a-number", b"\xff"])
def test_malformed_memory_is_unavailable_without_losing_other_metrics(tmp_path, content):
    directory = make_cgroup(tmp_path)
    (directory / "memory.current").write_bytes(content)
    [observation] = telemetry(tmp_path, unit_reader(), Clock()).observe_all([Workload(name="job", unit="job.service")])
    assert observation.memory_usage_bytes is None
    assert observation.cpu_pressure_some_avg10 == 12.5


def test_cgroup_symlink_escape_is_not_read(tmp_path):
    outside = make_cgroup(tmp_path / "outside")
    root = tmp_path / "root"
    link = root / CGROUP.lstrip("/")
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    [observation] = telemetry(root, unit_reader(), Clock()).observe_all([Workload(name="job", unit="job.service")])
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None


def test_missing_invocation_identity_has_no_metrics(tmp_path):
    make_cgroup(tmp_path)
    def reader(unit, manager):
        properties = unit_reader()(unit, manager)
        properties.pop("InvocationID")
        return properties
    [observation] = telemetry(tmp_path, reader, Clock()).observe_all([Workload(name="job", unit="job.service")])
    assert observation.cpu_usage_percent is None
    assert observation.memory_usage_bytes is None


def test_counter_interval_includes_time_spent_resolving_final_unit(tmp_path):
    directory = make_cgroup(tmp_path)
    clock = Clock()
    def reader(unit, manager):
        clock.now += 0.5
        return unit_reader()(unit, manager)
    advance = lambda: (directory / "cpu.stat").write_text("usage_usec 1250000\n")
    [observation] = telemetry(tmp_path, reader, clock, advance).observe_all([Workload(name="job", unit="job.service")])
    assert observation.cpu_usage_percent == pytest.approx(25)


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_sampling_intervals_are_rejected(interval):
    with pytest.raises(ValueError, match="finite and positive"):
        LinuxTelemetry(interval=interval)


def test_non_linux_has_no_default_live_collector(monkeypatch):
    monkeypatch.setattr(status_service.sys, "platform", "darwin")
    assert status_service.default_telemetry() is None


def test_linux_without_cgroup_v2_has_no_default_live_collector(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(status_service.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert status_service.default_telemetry() is None
