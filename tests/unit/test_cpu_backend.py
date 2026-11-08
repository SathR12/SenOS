import subprocess

import pytest

from senos.contracts import Workload
from senos.executor.cpu import SystemdCPUBackend
from senos.recovery.errors import RestoreError
from senos.workloads.systemd import SystemdResolver, UnitInfo


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(SystemdResolver, "resolve", lambda *args: UnitInfo("job.service", "/user.slice/job.service"))
    return SystemdCPUBackend()


@pytest.mark.parametrize("raw,expected", [("[not set]", None), ("100", 100)])
def test_cpu_backend_reads_explicit_and_unset(backend, monkeypatch, raw, expected):
    monkeypatch.setattr(backend, "_run", lambda *args: "CPUWeight=" + raw + "\nInvocationID=" + "a" * 32 + "\nControlGroup=/user.slice/job.service\n")
    assert backend.read(Workload(name="job", unit="job.service")).weight == expected


@pytest.mark.parametrize("raw", ["", "0", "idle", "10001"])
def test_cpu_backend_never_guesses_unsupported_original(backend, monkeypatch, raw):
    monkeypatch.setattr(backend, "_run", lambda *args: "CPUWeight=" + raw + "\nInvocationID=" + "a" * 32 + "\nControlGroup=/user.slice/job.service\n")
    with pytest.raises(RestoreError):
        backend.read(Workload(name="job", unit="job.service"))


def test_cpu_backend_writes_only_runtime_cpuweight_without_shell(backend, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("senos.executor.cpu.subprocess.run", run)
    backend.write(Workload(name="job", unit="job.service", manager="system"), None)
    arguments, options = calls[0]
    assert arguments == ["systemctl", "--system", "--no-ask-password", "set-property", "--runtime", "--", "job.service", "CPUWeight="]
    assert options.get("shell", False) is False


@pytest.mark.parametrize("weight", [True, 0, 10001, "100"])
def test_cpu_backend_rejects_invalid_recorded_values(backend, weight):
    with pytest.raises(RestoreError):
        backend.write(Workload(name="job", unit="job.service"), weight)
