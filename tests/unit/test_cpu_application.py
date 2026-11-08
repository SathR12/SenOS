from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from senos.agents.cpu import CPUAgent, CPUContext
from senos.contracts import Observation
from senos.executor.application import CPUApplication
from senos.executor.cpu import CPUState
from senos.intent import IntentService
from senos.recovery.errors import RestoreError
from senos.recovery.restore import RestoreService
from senos.storage.changes import CPUJournal
from senos.telemetry.linux import CPUWeightReading
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, "/app.slice/" + unit)


class Backend:
    def __init__(self, workloads):
        self.states = {w.workload_id: CPUState(100, "a" * 32, "/app.slice/" + w.unit) for w in workloads}
        self.writes = []
        self.fail = False
    def read(self, workload):
        return self.states[workload.workload_id]
    def write(self, workload, weight):
        self.writes.append((workload.workload_id, weight))
        if self.fail:
            self.fail = False
            raise RestoreError("write denied")
        self.states[workload.workload_id] = replace(self.read(workload), weight=weight)
    def read_cpu_weight(self, workload):
        state = self.read(workload)
        return CPUWeightReading(state.weight if state.weight is not None else 100, state.invocation_id, Path("/sys/fs/cgroup" + state.cgroup))


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    registry.register("web", "web.service")
    registry.register("build", "build.service")
    workloads = tuple(registry.list_workloads())
    backend = Backend(workloads)
    clock = [datetime.now(timezone.utc)]
    intent = IntentService(registry).prioritize("web", 600, ("build",)).model_copy(update={"submitted_at": clock[0]})
    application = CPUApplication(database, backend=backend, telemetry=backend,
        recovery=lambda db, session_id, seconds: application.store.ready(session_id), now=lambda: clock[0])
    def evaluation():
        observations = tuple(Observation(workload_id=w.workload_id, observed_at=clock[0] - timedelta(seconds=i),
            state="active", cpu_usage_percent=50, cpu_pressure_some_avg10=20)
            for w in workloads for i in (2, 1, 0))
        context = CPUContext(intent=intent, workloads=workloads, observations=observations,
            current_cpu_weights={w.workload_id: backend.read_cpu_weight(w).weight for w in workloads})
        return {"context": context.model_dump(mode="json"), "decision": CPUAgent().evaluate(context, now=clock[0]).model_dump(mode="json")}
    return database, registry, backend, application, intent, evaluation, clock


def test_applies_and_restores_with_durable_original_and_action_audit(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    assert CPUJournal(database).pending()[0]["original_weight"] == 100
    result = application.apply(session, evaluate())
    assert result.status.value == "applied" and result.effective_cpu_weight == 200
    assert backend.read(registry.get_by_name("build")).weight == 100
    assert application.store.get(session)["last_change_at"]
    application.finish(session)
    assert backend.read(registry.get_by_name("web")).weight == 100
    assert CPUJournal(database).pending() == []
    assert application.store.get(session)["status"] == "restored"


def test_unset_original_configuration_is_restored_without_guessing(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    target = registry.get_by_name("web")
    backend.states[target.workload_id] = replace(backend.read(target), weight=None)
    session = application.begin(intent)
    assert CPUJournal(database).pending()[0]["original_weight"] is None
    result = application.apply(session, evaluate())
    assert result.previous_cpu_weight == 100 and result.effective_cpu_weight == 200
    application.finish(session)
    assert backend.read(target).weight is None


def test_persistent_cooldown_and_original_relative_ceiling_prevent_ratchet(setup):
    _, _, _, application, intent, evaluate, clock = setup
    session = application.begin(intent)
    application.apply(session, evaluate())
    assert "cooldown" in application.apply(session, evaluate()).reason
    clock[0] += timedelta(seconds=31)
    assert "ceiling" in application.apply(session, evaluate()).reason
    application.finish(session)


def test_pending_recovery_prevents_conflicting_objectives(setup):
    _, _, _, application, intent, _, _ = setup
    session = application.begin(intent)
    with pytest.raises(RestoreError, match="pending recovery"):
        application.begin(intent)
    application.finish(session)


def test_worker_failure_prevents_any_cpu_write(setup):
    database, _, backend, application, intent, _, _ = setup
    def unavailable(*args):
        raise RestoreError("Recovery unavailable")
    application.recovery = unavailable
    with pytest.raises(RestoreError, match="Recovery unavailable"):
        application.begin(intent)
    assert backend.writes == [] and CPUJournal(database).pending() == []


def test_restarted_instance_is_never_modified_and_recovery_stays_pending(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    target = registry.get_by_name("web")
    backend.states[target.workload_id] = replace(backend.read(target), invocation_id="b" * 32)
    result = application.apply(session, evaluate())
    assert result.status.value == "rejected" and backend.writes == []
    with pytest.raises(RestoreError, match="recovery failed"):
        application.finish(session)
    assert CPUJournal(database).pending()


def test_external_configuration_is_not_silently_overwritten_by_automatic_restore(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    application.apply(session, evaluate())
    target = registry.get_by_name("web")
    backend.states[target.workload_id] = replace(backend.read(target), weight=300)
    with pytest.raises(RestoreError, match="outside SenOS"):
        application.finish(session)
    assert backend.read(target).weight == 300 and CPUJournal(database).pending()
    # Explicit restoration remains available after user review.
    RestoreService(database, backend).restore("web")
    assert application.store.get(session)["status"] == "restored"


def test_failed_write_recovers_original_and_reports_failure(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    backend.fail = True
    result = application.apply(session, evaluate())
    assert result.status.value == "failed"
    assert backend.read(registry.get_by_name("web")).weight == 100
    assert CPUJournal(database).pending() == []


def test_manual_restore_revokes_execution_ownership(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    application.apply(session, evaluate())
    RestoreService(database, backend).restore("web")
    assert application.apply(session, evaluate()).status.value == "rejected"
    assert backend.read(registry.get_by_name("web")).weight == 100


def test_expired_intent_cannot_acquire_execution(setup):
    _, _, backend, application, intent, _, clock = setup
    clock[0] += timedelta(seconds=601)
    with pytest.raises(RestoreError, match="five seconds"):
        application.begin(intent)
    assert backend.writes == []


def test_different_controller_cannot_reuse_active_execution_ownership(setup):
    _, _, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    connection = application.store.connect()
    try:
        connection.execute("UPDATE cpu_execution_sessions SET owner_pid=-1 WHERE session_id=?", (session,))
        connection.commit()
    finally:
        connection.close()
    result = application.apply(session, evaluate())
    assert result.status.value == "rejected" and "different controller" in result.reason
    assert backend.writes == []
    application.finish(session)


def test_write_before_crash_is_recoverable_without_a_success_audit_record(setup):
    database, registry, backend, application, intent, evaluate, _ = setup
    session = application.begin(intent)
    original = backend.write
    def interrupted(workload, weight):
        original(workload, weight)
        raise SystemExit("simulated abrupt controller termination")
    backend.write = interrupted
    with pytest.raises(SystemExit):
        application.apply(session, evaluate())
    assert backend.read(registry.get_by_name("web")).weight == 200
    assert CPUJournal(database).pending()
    backend.write = original
    application.finish(session)
    assert backend.read(registry.get_by_name("web")).weight == 100
    assert CPUJournal(database).pending() == []
