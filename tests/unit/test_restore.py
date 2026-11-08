from dataclasses import replace
import json

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.executor.cpu import CPUState
from senos.recovery.restore import RestoreError, RestoreService
from senos.workloads.registry import Registry, RegistrationError
from senos.workloads.systemd import UnitInfo


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, "/user.slice/" + unit)


class Backend:
    def __init__(self):
        self.state = CPUState(100, "a" * 32, "/user.slice/job.service")
        self.writes = []
        self.fail = False

    def read(self, workload):
        return self.state

    def write(self, workload, weight):
        self.writes.append(weight)
        if self.fail:
            raise RestoreError("write denied")
        self.state = replace(self.state, weight=weight)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "registry.sqlite3"
    Registry(path, Resolver()).register("job", "job.service")
    backend = Backend()
    return path, backend, RestoreService(path, backend)


def test_restore_persists_original_across_instances(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    result = RestoreService(path, backend).restore("job")
    assert result[0].status == "restored"
    assert backend.state.weight == 100
    assert RestoreService(path, backend).restore("job") == []
    assert len(Registry(path, Resolver()).list_workloads()) == 1


def test_repeated_capture_keeps_first_original(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    service.capture("job")
    backend.state = replace(backend.state, weight=500)
    service.restore()
    assert backend.state.weight == 100


def test_dry_run_does_not_write_or_clear_journal(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    assert service.restore(dry_run=True)[0].status == "dry_run"
    assert backend.writes == []
    assert service.restore()[0].status == "restored"


def test_unset_original_is_preserved(setup):
    path, backend, service = setup
    backend.state = replace(backend.state, weight=None)
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    service.restore()
    assert backend.state.weight is None


def test_failures_remain_pending_and_can_retry(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    backend.fail = True
    assert service.restore()[0].status == "failed"
    backend.fail = False
    assert service.restore()[0].status == "restored"


def test_restarted_unit_is_not_modified(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300, invocation_id="b" * 32)
    assert service.restore()[0].status == "failed"
    assert backend.writes == []
    with pytest.raises(RegistrationError, match="restore"):
        Registry(path).unregister("job")


def test_pending_restore_blocks_single_and_all_removal_atomically(setup):
    path, backend, service = setup
    Registry(path, Resolver()).register("other", "other.service")
    service.capture("job")
    with pytest.raises(RegistrationError, match="restore"):
        Registry(path).unregister("job")
    with pytest.raises(RegistrationError, match="restore"):
        Registry(path).unregister(all_workloads=True)
    assert len(Registry(path).list_workloads()) == 2
    service.restore()
    assert Registry(path).unregister(all_workloads=True) == 2


def test_no_journal_is_honest_noop_and_does_not_create_database(tmp_path):
    path = tmp_path / "absent" / "registry.sqlite3"
    assert RestoreService(path, Backend()).restore() == []
    assert not path.parent.exists()


def test_unknown_workload_is_error(setup):
    path, backend, service = setup
    with pytest.raises(RestoreError, match="not registered"):
        service.restore("missing")


def test_cli_no_changes_and_invalid_selection(tmp_path):
    runner = CliRunner()
    path = str(tmp_path / "missing.sqlite3")
    result = runner.invoke(app, ["restore", "--all", "--db", path])
    assert result.exit_code == 0, result.output
    assert "No recorded changes" in result.output
    assert runner.invoke(app, ["restore", "job", "--all", "--db", path]).exit_code == 2


def test_cli_json_and_failure_exit(setup, monkeypatch):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    backend.fail = True
    monkeypatch.setattr("senos.recovery.restore.SystemdCPUBackend", lambda: backend)
    result = CliRunner().invoke(app, ["restore", "job", "--db", str(path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["results"][0]["status"] == "failed"


def test_verification_failure_stays_pending(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    backend.write = lambda workload, weight: None
    assert service.restore()[0].status == "failed"
    with pytest.raises(RegistrationError):
        Registry(path).unregister("job")


def test_partial_write_failure_can_be_reconciled_without_second_write(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, weight=300)
    def partial(workload, weight):
        backend.state = replace(backend.state, weight=weight)
        raise RestoreError("verification interrupted")
    backend.write = partial
    assert service.restore()[0].status == "failed"
    # The original value is already back, but the journal still requires verification.
    assert service.restore()[0].status == "restored"


def test_restore_all_continues_after_one_target_fails(setup):
    path, backend, service = setup
    Registry(path, Resolver()).register("other", "other.service")
    service.capture("job")
    service.capture("other")
    backend.state = replace(backend.state, weight=300)
    original_write = backend.write
    def selective(workload, weight):
        if workload.name == "job":
            raise RestoreError("denied")
        original_write(workload, weight)
    backend.write = selective
    results = service.restore()
    assert [result.status for result in results] == ["failed", "restored"]


def test_capture_cannot_replace_pending_original_after_restart(setup):
    path, backend, service = setup
    service.capture("job")
    backend.state = replace(backend.state, invocation_id="b" * 32)
    with pytest.raises(RestoreError, match="earlier invocation"):
        service.capture("job")


def test_restore_dry_run_does_not_modify_database_bytes(setup):
    path, backend, service = setup
    service.capture("job")
    before = path.read_bytes()
    service.restore(dry_run=True)
    assert path.read_bytes() == before


def test_invalid_saved_workload_does_not_reach_executor(setup):
    import sqlite3
    path, backend, service = setup
    service.capture("job")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE registered_workloads SET unit='../bad.service'")
    assert service.restore()[0].status == "failed"
    assert backend.writes == []


def test_concurrently_replaced_restore_point_is_not_applied(setup, monkeypatch):
    path, backend, service = setup
    service.capture("job")
    stale = service.journal.pending()
    service.restore()
    backend.state = replace(backend.state, weight=200)
    service.capture("job")
    monkeypatch.setattr(service.journal, "pending", lambda: stale)
    assert service.restore()[0].status == "failed"
    assert backend.writes == []
