import json

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.workloads.registry import Registry, RegistrationError
from senos.workloads.systemd import UnitInfo


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit=unit, cgroup="/user.slice/" + unit)


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path / "registry.sqlite3", Resolver())


def test_remove_one_preserves_other_registrations_and_persists(registry):
    registry.register("one", "one.service")
    second, _ = registry.register("two", "two.service")
    assert registry.unregister("one") == 1
    assert Registry(registry.store.path, Resolver()).list_workloads() == [second]


def test_remove_all_and_repeat_on_empty_registry(registry):
    registry.register("one", "one.service")
    registry.register("two", "two.service")
    assert registry.unregister(all_workloads=True) == 2
    assert registry.list_workloads() == []
    assert registry.unregister(all_workloads=True) == 0


def test_unknown_name_is_error_and_preserves_existing_data(registry):
    original, _ = registry.register("one", "one.service")
    with pytest.raises(RegistrationError, match="not registered"):
        registry.unregister("missing")
    assert registry.list_workloads() == [original]


@pytest.mark.parametrize("arguments", [{}, {"name": "one", "all_workloads": True}, {"name": " "}])
def test_invalid_removal_never_creates_database(registry, arguments):
    with pytest.raises(RegistrationError):
        registry.unregister(**arguments)
    assert not registry.store.path.exists()


def test_remove_from_missing_database_does_not_create_it(registry):
    assert registry.unregister(all_workloads=True) == 0
    with pytest.raises(RegistrationError, match="not registered"):
        registry.unregister("one")
    assert not registry.store.path.exists()


def test_unregister_does_not_require_running_workload_or_systemd(registry):
    registry.register("one", "one.service")
    class Unavailable:
        def resolve(self, unit, manager):
            pytest.fail("Unregister must not contact systemd")
    registry.resolver = Unavailable()
    assert registry.unregister("one") == 1


@pytest.fixture
def command(tmp_path):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    registry.register("one", "one.service")
    registry.register("two", "two.service")
    return CliRunner(), registry, ["unregister", "--db", str(database)]


def test_cli_removes_one_and_outputs_json(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["one", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"removed": 1}
    assert [workload.name for workload in registry.list_workloads()] == ["two"]


def test_cli_clear_decline_preserves_everything(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["--all"], input="n\n")
    assert result.exit_code != 0
    assert len(registry.list_workloads()) == 2


def test_cli_clear_requires_confirmation(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["--all"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Unregistered 2 workloads" in result.output
    assert registry.list_workloads() == []


def test_cli_clear_without_interactive_input_does_not_remove(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["--all"])
    assert result.exit_code != 0
    assert len(registry.list_workloads()) == 2


def test_cli_explicit_yes_supports_scripts_and_clean_json(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["--all", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"removed": 2}
    assert registry.list_workloads() == []


@pytest.mark.parametrize("options", [[], ["one", "--all"]])
def test_cli_invalid_target_returns_usage_error_without_removing(command, options):
    runner, registry, args = command
    result = runner.invoke(app, args + options)
    assert result.exit_code == 2
    assert len(registry.list_workloads()) == 2


def test_cli_unknown_name_is_readable(command):
    runner, registry, args = command
    result = runner.invoke(app, args + ["missing"])
    assert result.exit_code == 1
    assert "not registered" in result.output
    assert len(registry.list_workloads()) == 2


def test_removed_name_can_be_registered_again_with_new_identity(registry):
    old, _ = registry.register("one", "one.service")
    registry.unregister("one")
    new, created = registry.register("one", "other.service")
    assert created and new.id != old.id
