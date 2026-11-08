import json
import sqlite3

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.contracts import Observation, Workload
from senos.storage.registry import RegistryStore
from senos.telemetry import fake, status as status_service
from senos.telemetry.status import get_status
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit=unit, cgroup="/user.slice/" + unit)


@pytest.fixture(autouse=True)
def no_live_telemetry(monkeypatch):
    # Keep these registry tests identical on macOS and Linux; live readings
    # are covered in test_telemetry.py and the opt-in systemd tests.
    monkeypatch.setattr(status_service, "default_telemetry", lambda: None)


def test_empty_registry_is_not_created_by_status(tmp_path):
    database = tmp_path / "absent" / "registry.sqlite3"
    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 0, result.output
    assert "No workloads registered" in result.output
    assert "Live telemetry requires Linux" in result.output
    assert not database.parent.exists()


def test_real_status_lists_saved_workloads_without_fake_readings(tmp_path, monkeypatch):
    database = tmp_path / "registry.sqlite3"
    Registry(database, Resolver()).register("my-job", "my-job.scope", "system")
    def fail():
        pytest.fail("Default status must never read demo data")
    monkeypatch.setattr(fake, "list_workloads", fail)
    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 0, result.output
    assert "my-job.scope" in result.output
    assert "system" in result.output
    assert "unknown" in result.output
    assert "N/A" in result.output
    assert "editor" not in result.output


def test_status_json_preserves_registry_identity_and_unavailable_values(tmp_path):
    database = tmp_path / "registry.sqlite3"
    record, _ = Registry(database, Resolver()).register("my-job", "my-job.service")
    result = CliRunner().invoke(app, ["status", "--json", "--db", str(database)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "registry"
    assert payload["live_telemetry"] is False
    row = payload["workloads"][0]
    assert row["workload"]["workload_id"] == record.id
    assert row["observation"]["workload_id"] == record.id
    assert row["observation"]["cpu_usage_percent"] is None
    assert row["observation"]["memory_usage_bytes"] is None
    assert row["observation"]["state"] == "unknown"


def test_status_honors_registry_environment_variable(tmp_path):
    database = tmp_path / "registry.sqlite3"
    Registry(database, Resolver()).register("selected", "selected.service")
    result = CliRunner().invoke(app, ["status"], env={"SENOS_REGISTRY_PATH": str(database)})
    assert result.exit_code == 0, result.output
    assert "selected.service" in result.output


def test_demo_is_labeled_and_does_not_access_the_registry(tmp_path, monkeypatch):
    def fail(*args):
        pytest.fail("Demo must not access the registry")
    monkeypatch.setattr(RegistryStore, "list_workloads", fail)
    result = CliRunner().invoke(app, ["status", "--demo", "--db", str(tmp_path / "missing.sqlite3")])
    assert result.exit_code == 0, result.output
    assert "DEMO: synthetic" in result.output
    assert "editor" in result.output and "build" in result.output
    assert "MEM MiB" in result.output
    assert "300" in result.output and "1200" in result.output
    assert "12.0" in result.output and "85.0" in result.output
    assert not (tmp_path / "missing.sqlite3").exists()


def test_demo_json_is_tagged_and_uses_shared_models(tmp_path):
    result = CliRunner().invoke(app, ["status", "--demo", "--json", "--db", str(tmp_path / "missing.sqlite3")])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "demo"
    assert payload["live_telemetry"] is False
    for row in payload["workloads"]:
        workload = Workload.model_validate(row["workload"])
        observation = Observation.model_validate(row["observation"])
        assert workload.workload_id == observation.workload_id


def test_fake_list_is_a_copy_and_identities_are_stable():
    workloads = fake.list_workloads()
    assert len(workloads) == 2
    workloads.clear()
    assert len(fake.list_workloads()) == 2
    first = fake.list_workloads()[0]
    assert fake.observe(first).workload_id == first.workload_id
    assert fake.observe(first).memory_usage_bytes == 300 * 1024**2


def test_fake_rejects_non_demo_identities():
    with pytest.raises(ValueError, match="No demo reading"):
        fake.observe(Workload(name="real-job", unit="real-job.service"))


def test_status_database_failure_is_readable(tmp_path):
    database = tmp_path / "bad.sqlite3"
    database.write_text("not a database")
    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 1
    assert "Cannot read workload registry" in result.output
    assert "Traceback" not in result.output


def test_status_invalid_saved_contract_is_readable(tmp_path):
    database = tmp_path / "registry.sqlite3"
    Registry(database, Resolver()).register("job", "job.service")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE registered_workloads SET id = 'invalid'")
    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 1
    assert "Cannot read workload registry" in result.output


def test_removal_is_reflected_in_status(tmp_path):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    registry.register("job", "job.service")
    assert len(get_status(database).rows) == 1
    registry.unregister("job")
    assert get_status(database).rows == ()


def test_status_never_creates_schema_in_an_existing_empty_database(tmp_path):
    database = tmp_path / "empty.sqlite3"
    with sqlite3.connect(database):
        pass
    result = CliRunner().invoke(app, ["status", "--db", str(database)])
    assert result.exit_code == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
