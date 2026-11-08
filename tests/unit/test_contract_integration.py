from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from senos.contracts import ActionResult, Intent, Observation, Proposal, Workload
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo, validate_unit


@pytest.mark.parametrize("value", [True, False, "200", 200.0, 1.5])
def test_proposals_reject_coerced_integer_weights(value):
    with pytest.raises(ValidationError):
        Proposal(intent_id=uuid4(), workload_id=uuid4(), expected_cpu_weight=100, proposed_cpu_weight=value, rationale="test", expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))


@pytest.mark.parametrize("value", [True, "60", 60.0, 1.5])
def test_intents_reject_coerced_integer_durations(value):
    with pytest.raises(ValidationError):
        Intent(kind="prioritize", target_workload_id=uuid4(), duration_seconds=value, source_text="test")


@pytest.mark.parametrize("value", [True, "1024", 1024.0, 1.5])
def test_observation_rejects_coerced_byte_counts(value):
    with pytest.raises(ValidationError):
        Observation(workload_id=uuid4(), state="active", memory_usage_bytes=value)


@pytest.mark.parametrize("state", ["active", "inactive", "failed", "unknown"])
def test_unavailable_metrics_are_distinct_from_measured_zero(state):
    missing = Observation(workload_id=uuid4(), state=state)
    assert missing.cpu_usage_percent is None
    assert missing.memory_usage_bytes is None
    measured = missing.model_copy(update={"cpu_usage_percent": 0.0, "memory_usage_bytes": 0})
    assert measured.cpu_usage_percent == 0
    assert measured.memory_usage_bytes == 0
    assert Observation.model_validate_json(missing.model_dump_json()) == missing


@pytest.mark.parametrize("unit", ["batch.scope", "worker@one.service", "_worker.service"])
def test_shared_workload_accepts_registry_targets(unit):
    workload = Workload(name="n" * 128, unit=unit, manager="system")
    assert workload.manager == "system"
    assert Workload.model_validate_json(workload.model_dump_json()) == workload
    validate_unit(unit)


@pytest.mark.parametrize("unit", ["worker@.service", "*.service", "../bad.service", r"C:\tmp\bad.service", "worker@@one.service"])
def test_shared_workload_rejects_invalid_unit_shapes(unit):
    with pytest.raises(ValidationError):
        Workload(name="worker", unit=unit)


@pytest.mark.parametrize("name", [" ", "line\nbreak", "x" * 129])
def test_shared_workload_rejects_invalid_display_names(name):
    with pytest.raises(ValidationError):
        Workload(name=name, unit="batch.service")


def test_registry_adapter_preserves_identity_timestamp_and_manager(tmp_path):
    class Resolver:
        def resolve(self, unit, manager):
            return UnitInfo(unit=unit, cgroup="/user.slice/" + unit)
    registry = Registry(tmp_path / "registry.sqlite3", Resolver())
    user, _ = registry.register("user-job", "batch.scope")
    system, _ = registry.register("system-job", "batch.scope", "system")
    for contract in [user, system]:
        reloaded = registry.get_by_id(contract.workload_id)
        assert reloaded == contract
        assert registry.get_by_name(contract.name) == contract
        assert contract.registered_at.tzinfo is not None
    assert user.manager != system.manager


def test_action_result_rejects_boolean_weight():
    with pytest.raises(ValidationError):
        ActionResult(proposal_id=uuid4(), workload_id=uuid4(), status="rejected", requested_cpu_weight=True, reason="test", started_at=datetime.now(timezone.utc))
