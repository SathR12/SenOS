from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from senos.agents.cpu import CPUAgent, CPUContext
from senos.cli import app
from senos.contracts import Intent, Observation, Workload

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


@pytest.fixture
def context():
    target = Workload(name="web", unit="web.service")
    other = Workload(name="build", unit="build.service")
    intent = Intent(kind="prioritize", target_workload_id=target.workload_id, preserve_workload_ids=(other.workload_id,), duration_seconds=600, source_text="Prioritize web while build continues", submitted_at=NOW - timedelta(seconds=30))
    observations = [Observation(workload_id=target.workload_id, observed_at=NOW - timedelta(seconds=age), state="active", cpu_usage_percent=70, cpu_pressure_some_avg10=15) for age in [4, 2, 0]]
    observations.append(Observation(workload_id=other.workload_id, observed_at=NOW, state="active", cpu_usage_percent=150))
    return CPUContext(intent=intent, workloads=(target, other), observations=tuple(observations), current_cpu_weights={target.workload_id: 100, other.workload_id: 100})


def test_sustained_contention_proposes_only_target_boost(context):
    decision = CPUAgent().evaluate(context, now=NOW)
    assert decision.status == "proposed"
    proposal = decision.proposal
    assert proposal.workload_id == context.intent.target_workload_id
    assert proposal.intent_id == context.intent.intent_id
    assert proposal.expected_cpu_weight == 100
    assert proposal.proposed_cpu_weight == 200
    assert proposal.expires_at == context.intent.submitted_at + timedelta(seconds=600)
    assert proposal.created_at == NOW


def test_high_utilization_without_pressure_is_not_sufficient(context):
    samples = tuple(sample.model_copy(update={"cpu_pressure_some_avg10": 0, "cpu_usage_percent": 400}) for sample in context.observations)
    decision = CPUAgent().evaluate(context.model_copy(update={"observations": samples}), now=NOW)
    assert decision.status == "no_action"
    assert decision.proposal is None


@pytest.mark.parametrize("change", ["missing_pressure", "stale", "future", "inactive", "one_sample", "duplicate_times", "memory_pressure", "missing_cpu"])
def test_insufficient_or_inappropriate_evidence_does_not_propose(context, change):
    target = context.intent.target_workload_id
    samples = list(context.observations)
    for i, sample in enumerate(samples):
        if sample.workload_id != target:
            continue
        updates = {}
        if change == "missing_pressure": updates["cpu_pressure_some_avg10"] = None
        if change == "missing_cpu": updates["cpu_usage_percent"] = None
        if change == "stale": updates["observed_at"] = NOW - timedelta(seconds=60+i)
        if change == "future": updates["observed_at"] = NOW + timedelta(seconds=1+i)
        if change == "inactive": updates["state"] = "inactive"
        if change == "duplicate_times": updates["observed_at"] = NOW
        if change == "memory_pressure": updates["memory_pressure_some_avg10"] = 20
        samples[i] = Observation.model_validate({**sample.model_dump(), **updates})
    if change == "one_sample":
        samples = [samples[-2], samples[-1]]
    decision = CPUAgent().evaluate(context.model_copy(update={"observations": tuple(samples)}), now=NOW)
    assert decision.status == "no_action", decision


def test_unknown_unregistered_target_is_not_proposed(context):
    intent = context.intent.model_copy(update={"target_workload_id": uuid4()})
    assert CPUAgent().evaluate(context.model_copy(update={"intent": intent}), now=NOW).status == "no_action"


@pytest.mark.parametrize("weight", [None, 1000, 2000])
def test_unknown_or_capped_weights_do_not_propose(context, weight):
    weights = dict(context.current_cpu_weights)
    weights[context.intent.target_workload_id] = weight
    assert CPUAgent().evaluate(context.model_copy(update={"current_cpu_weights": weights}), now=NOW).status == "no_action"


def test_expired_and_restore_intents_do_not_propose(context):
    assert CPUAgent().evaluate(context, now=NOW + timedelta(hours=1)).status == "no_action"
    intent = Intent(kind="restore", target_workload_id=context.intent.target_workload_id, source_text="Restore web", submitted_at=NOW)
    assert CPUAgent().evaluate(context.model_copy(update={"intent": intent}), now=NOW).status == "no_action"


def test_recent_change_causes_cooldown(context):
    updated = context.model_copy(update={"last_change_at": NOW - timedelta(seconds=5)})
    assert CPUAgent().evaluate(updated, now=NOW).status == "no_action"


def test_preserved_workload_must_be_registered_and_observed(context):
    missing = context.model_copy(update={"workloads": context.workloads[:1]})
    assert CPUAgent().evaluate(missing, now=NOW).status == "no_action"
    samples = tuple(item for item in context.observations if item.workload_id == context.intent.target_workload_id)
    assert CPUAgent().evaluate(context.model_copy(update={"observations": samples}), now=NOW).status == "no_action"


def test_integer_weights_reject_boolean_input(context):
    payload = context.model_dump()
    payload["current_cpu_weights"][context.intent.target_workload_id] = True
    with pytest.raises(ValidationError):
        CPUContext.model_validate(payload)


def test_cli_demo_is_explicitly_dry_run():
    import json
    result = CliRunner().invoke(app, ["agent", "cpu", "--demo", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "dry_run"
    assert payload["source"] == "demo"
    assert payload["decision"]["status"] == "proposed"


def test_cli_requires_one_source_and_reports_invalid_file(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["agent", "cpu"]).exit_code == 2
    assert runner.invoke(app, ["agent", "cpu", "--demo", "--input", str(tmp_path / "missing.json")]).exit_code == 2
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}")
    result = runner.invoke(app, ["agent", "cpu", "--input", str(invalid)])
    assert result.exit_code == 1
    assert "Invalid CPU context" in result.output


def test_agent_never_contacts_systemd_or_registry(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Proposal agent must not access OS controls or the registry")
    monkeypatch.setattr("senos.executor.cpu.SystemdCPUBackend.read", forbidden)
    monkeypatch.setattr("senos.executor.cpu.SystemdCPUBackend.write", forbidden)
    monkeypatch.setattr("senos.storage.registry.RegistryStore.list_workloads", forbidden)
    result = CliRunner().invoke(app, ["agent", "cpu", "--demo"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN (demo)" in result.output


def test_cli_evaluates_context_file(context, tmp_path):
    import json
    from senos.agents.demo import cpu_context
    path = tmp_path / "context.json"
    path.write_text(cpu_context().model_dump_json())
    result = CliRunner().invoke(app, ["agent", "cpu", "--input", str(path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "file"
    assert payload["decision"]["status"] == "proposed"


def test_agent_does_not_modify_inputs_and_caps_boost(context):
    weights = dict(context.current_cpu_weights)
    weights[context.intent.target_workload_id] = 900
    request = context.model_copy(update={"current_cpu_weights": weights})
    before = request.model_dump_json()
    assert CPUAgent().evaluate(request, now=NOW).proposal.proposed_cpu_weight == 1000
    assert request.model_dump_json() == before


def test_future_intent_and_naive_clock_are_not_used(context):
    intent = context.intent.model_copy(update={"submitted_at": NOW + timedelta(seconds=1)})
    assert CPUAgent().evaluate(context.model_copy(update={"intent": intent}), now=NOW).status == "no_action"
    with pytest.raises(ValueError, match="timezone"):
        CPUAgent().evaluate(context, now=NOW.replace(tzinfo=None))


def test_a_managed_active_peer_is_required_without_preserved_ids(context):
    intent = context.intent.model_copy(update={"preserve_workload_ids": ()})
    target_only = context.model_copy(update={"intent": intent, "workloads": context.workloads[:1]})
    assert CPUAgent().evaluate(target_only, now=NOW).status == "no_action"


def test_different_managers_do_not_count_as_comparable_peers(context):
    workloads = (context.workloads[0], context.workloads[1].model_copy(update={"manager": "system"}))
    assert CPUAgent().evaluate(context.model_copy(update={"workloads": workloads}), now=NOW).status == "no_action"


def test_duplicate_inventory_ids_are_rejected(context):
    payload = context.model_dump()
    payload["workloads"] = (context.workloads[0], context.workloads[0])
    with pytest.raises(ValidationError, match="unique"):
        CPUContext.model_validate(payload)
