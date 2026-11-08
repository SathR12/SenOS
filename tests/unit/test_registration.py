import json
import subprocess

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.workloads.registry import Registry, RegistrationError
from senos.workloads.systemd import SystemdResolver, UnitInfo


class FakeResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, unit, manager):
        self.calls.append((unit, manager))
        return UnitInfo(unit=unit, cgroup="/user.slice/" + unit)


def test_registration_persists_stable_identity(tmp_path):
    database = tmp_path / "state" / "registry.sqlite3"
    registry = Registry(database, FakeResolver())
    first, created = registry.register("batch", "batch.service")
    assert created
    second, created = Registry(database, FakeResolver()).register("batch", "batch.service")
    assert not created
    assert first == second
    assert Registry(database, FakeResolver()).list_workloads() == [first]


@pytest.mark.parametrize("name,unit,manager", [("batch", "other.service", "user"), ("other", "batch.service", "user"), ("batch", "batch.service", "system")])
def test_registration_rejects_conflicting_names_or_targets(tmp_path, name, unit, manager):
    registry = Registry(tmp_path / "registry.sqlite3", FakeResolver())
    registry.register("batch", "batch.service")
    with pytest.raises(RegistrationError, match="already registered"):
        registry.register(name, unit, manager)
    assert len(registry.list_workloads()) == 1


@pytest.mark.parametrize("name,unit", [(" ", "batch.service"), ("batch", "../bad.service"), ("batch", "*.service"), ("batch", "--help"), ("batch", "batch.service;reboot"), ("batch", "worker@.service")])
def test_invalid_inputs_never_invoke_systemd(tmp_path, name, unit):
    resolver = FakeResolver()
    with pytest.raises(RegistrationError):
        Registry(tmp_path / "registry.sqlite3", resolver).register(name, unit)
    assert resolver.calls == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_failed_resolution_does_not_create_registration(tmp_path):
    class Missing:
        def resolve(self, unit, manager):
            raise RegistrationError("Unit is not loaded")
    database = tmp_path / "registry.sqlite3"
    with pytest.raises(RegistrationError):
        Registry(database, Missing()).register("missing", "missing.service")
    assert not database.exists()


def test_aliases_use_canonical_unit_identity(tmp_path):
    class Alias:
        def resolve(self, unit, manager):
            return UnitInfo(unit="canonical.service", cgroup="/user.slice/canonical.service")
    registry = Registry(tmp_path / "registry.sqlite3", Alias())
    first, _ = registry.register("batch", "alias.service")
    second, created = registry.register("batch", "canonical.service")
    assert first == second and not created


def test_cli_register_outputs_record_and_duplicate_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(SystemdResolver, "resolve", FakeResolver().resolve)
    runner = CliRunner()
    args = ["register", "batch", "--unit", "batch.service", "--db", str(tmp_path / "registry.sqlite3"), "--json"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert payload["workload"]["manager"] == "user"
    repeat = runner.invoke(app, args)
    assert repeat.exit_code == 0
    assert json.loads(repeat.output)["workload"]["id"] == payload["workload"]["id"]
    assert json.loads(repeat.output)["created"] is False


def test_cli_error_is_readable_and_nonzero(tmp_path, monkeypatch):
    def missing(self, unit, manager):
        raise RegistrationError("Unit is not active")
    monkeypatch.setattr(SystemdResolver, "resolve", missing)
    result = CliRunner().invoke(app, ["register", "batch", "--unit", "batch.service", "--db", str(tmp_path / "registry.sqlite3")])
    assert result.exit_code == 1
    assert "Unit is not active" in result.output
    assert "Traceback" not in result.output


def test_system_option_uses_system_manager(tmp_path, monkeypatch):
    fake = FakeResolver()
    monkeypatch.setattr(SystemdResolver, "resolve", fake.resolve)
    result = CliRunner().invoke(app, ["register", "batch", "--unit", "batch.service", "--system", "--db", str(tmp_path / "registry.sqlite3")])
    assert result.exit_code == 0, result.output
    assert fake.calls == [("batch.service", "system")]


def test_resolver_uses_read_only_argv_without_a_shell(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "Id=batch.service\nLoadState=loaded\nActiveState=active\nControlGroup=/user.slice/batch.service\n", "")
    monkeypatch.setattr("senos.workloads.systemd.sys.platform", "linux")
    monkeypatch.setattr("senos.workloads.systemd.subprocess.run", run)
    result = SystemdResolver().resolve("batch.service", "user")
    assert result.unit == "batch.service"
    args, kwargs = calls[0]
    assert args[:3] == ["systemctl", "--user", "show"]
    assert args[-2:] == ["--", "batch.service"]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] == 5


@pytest.mark.parametrize("output", ["Id=batch.service\nLoadState=not-found\n", "Id=batch.service\nLoadState=loaded\nActiveState=inactive\n", "Id=batch.service\nLoadState=loaded\nActiveState=active\nControlGroup=/\n", "Id=batch.service\nLoadState=loaded\nActiveState=active\nControlGroup=/user.slice/../bad\n"])
def test_resolver_rejects_missing_inactive_or_invalid_cgroups(monkeypatch, output):
    monkeypatch.setattr("senos.workloads.systemd.sys.platform", "linux")
    monkeypatch.setattr("senos.workloads.systemd.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""))
    with pytest.raises(RegistrationError):
        SystemdResolver().resolve("batch.service", "user")


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 5)])
def test_resolver_reports_missing_tools_and_timeouts(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("senos.workloads.systemd.sys.platform", "linux")
    monkeypatch.setattr("senos.workloads.systemd.subprocess.run", fail)
    with pytest.raises(RegistrationError):
        SystemdResolver().resolve("batch.service", "user")


def test_cli_database_errors_are_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(SystemdResolver, "resolve", FakeResolver().resolve)
    result = CliRunner().invoke(app, ["register", "batch", "--unit", "batch.service", "--db", str(tmp_path)])
    assert result.exit_code == 1
    assert "Cannot access registry database" in result.output


def test_cli_honors_database_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(SystemdResolver, "resolve", FakeResolver().resolve)
    database = tmp_path / "registry.sqlite3"
    result = CliRunner().invoke(app, ["register", "batch", "--unit", "batch.service"], env={"SENOS_REGISTRY_PATH": str(database)})
    assert result.exit_code == 0, result.output
    assert database.exists()


def test_default_path_respects_absolute_xdg_state_home(tmp_path, monkeypatch):
    from senos.config import default_registry_path
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_registry_path() == tmp_path / "senos" / "registry.sqlite3"


def test_default_path_ignores_relative_xdg_state_home(monkeypatch):
    from pathlib import Path
    from senos.config import default_registry_path
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    assert default_registry_path() == Path.home() / ".local" / "state" / "senos" / "registry.sqlite3"


def test_resolver_reports_systemd_manager_failure(monkeypatch):
    monkeypatch.setattr("senos.workloads.systemd.sys.platform", "linux")
    monkeypatch.setattr("senos.workloads.systemd.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "Failed to connect to bus"))
    with pytest.raises(RegistrationError, match="session bus"):
        SystemdResolver().resolve("batch.service", "user")


def test_resolver_reports_unsupported_platform(monkeypatch):
    monkeypatch.setattr("senos.workloads.systemd.sys.platform", "win32")
    with pytest.raises(RegistrationError, match="requires Linux"):
        SystemdResolver().resolve("batch.service", "user")
