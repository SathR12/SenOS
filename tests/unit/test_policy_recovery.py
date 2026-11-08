from dataclasses import replace
from datetime import datetime, timedelta, timezone

from senos.agents.cpu import CPUPolicy
from senos.contracts import Intent, Proposal
from senos.executor.cpu import CPUState
from senos.executor.service import ProposalExecutor
from senos.policy.service import ProposalValidator
from senos.recovery.restore import RestoreService
from senos.storage.persistence import PolicyStore
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo


NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, f"/user.slice/{unit}")


class Backend:
    def __init__(self):
        self.state = CPUState(100, "a" * 32, "/user.slice/job.service")
        self.writes = []

    def read(self, workload):
        return self.state

    def write(self, workload, weight):
        self.writes.append(weight)
        self.state = replace(self.state, weight=weight)


def setup(tmp_path):
    database = tmp_path / "policy.sqlite3"
    workload, _ = Registry(database, Resolver()).register("job", "job.service")
    intent = Intent(
        kind="prioritize",
        target_workload_id=workload.workload_id,
        duration_seconds=600,
        source_text="prioritize job",
        submitted_at=NOW,
    )
    proposal = Proposal(
        intent_id=intent.intent_id,
        workload_id=workload.workload_id,
        expected_cpu_weight=100,
        proposed_cpu_weight=200,
        rationale="contention",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    store = PolicyStore(database)
    store.save_intent(intent)
    store.save_proposal(proposal)
    return database, workload, intent, proposal, Backend()


def test_manual_restore_is_idempotent_for_durable_action(tmp_path):
    database, workload, intent, proposal, backend = setup(tmp_path)
    validation = ProposalValidator(CPUPolicy()).validate(
        proposal, intent, workload, backend.state, now=NOW
    )
    result = ProposalExecutor(database, backend).execute(
        proposal, workload, validation, dry_run=False, now=NOW
    )
    assert result.status == "applied"
    assert backend.state.weight == 200

    service = RestoreService(database, backend)
    restored = service.reconcile("job", force=True, now=NOW)
    assert restored[0].status == "restored"
    assert backend.state.weight == 100
    assert service.reconcile("job", force=True, now=NOW) == []


def test_reconcile_restores_after_crash_before_action_result(tmp_path):
    database, workload, intent, proposal, backend = setup(tmp_path)

    original_save_result = PolicyStore.save_action_result
    def crash_before_result(store, result):
        raise RuntimeError("controller crashed")

    PolicyStore.save_action_result = crash_before_result
    try:
        validation = ProposalValidator().validate(
            proposal, intent, workload, backend.state, now=NOW
        )
        try:
            ProposalExecutor(database, backend).execute(
                proposal, workload, validation, dry_run=False, now=NOW
            )
        except RuntimeError:
            pass
    finally:
        PolicyStore.save_action_result = original_save_result

    assert backend.state.weight == 200
    restored = RestoreService(database, backend).reconcile(
        "job", now=NOW + timedelta(minutes=11)
    )
    assert restored[0].status == "restored"
    assert backend.state.weight == 100
