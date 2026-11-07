from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from senos.contracts import (
    ActionResult,
    ActionStatus,
    Intent,
    IntentKind,
    Observation,
    Proposal,
    Workload,
    WorkloadState,
)


NOW = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def test_workload_represents_a_registered_service() -> None:
    workload = Workload(name="Interactive editor", unit="editor@alex.service")

    assert workload.name == "Interactive editor"
    assert workload.unit == "editor@alex.service"
    assert workload.workload_id.version == 4
    assert workload.registered_at.tzinfo is not None


@pytest.mark.parametrize(
    "unit",
    ["editor", "/etc/systemd/system/editor.service", "editor timer.service"],
)
def test_workload_rejects_unmanaged_unit_shapes(unit: str) -> None:
    with pytest.raises(ValidationError):
        Workload(name="Editor", unit=unit)


def test_contracts_are_immutable_and_reject_unknown_fields() -> None:
    workload = Workload(name="Editor", unit="editor.service")

    with pytest.raises(ValidationError):
        workload.name = "Changed"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        Workload(name="Editor", unit="editor.service", arbitrary=True)


def test_observation_captures_workload_health_vitals() -> None:
    observation = Observation(
        workload_id=uuid4(),
        observed_at=NOW,
        state=WorkloadState.ACTIVE,
        cpu_usage_percent=175.5,
        memory_usage_bytes=512_000_000,
        cpu_pressure_some_avg10=2.4,
        memory_pressure_some_avg10=0.5,
        memory_pressure_full_avg10=0.0,
    )

    assert observation.cpu_usage_percent == 175.5
    assert observation.memory_usage_bytes == 512_000_000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_usage_percent", -0.1),
        ("memory_usage_bytes", -1),
        ("cpu_pressure_some_avg10", 100.1),
    ],
)
def test_observation_rejects_invalid_measurements(field: str, value: float) -> None:
    values = {
        "workload_id": uuid4(),
        "observed_at": NOW,
        "state": "active",
        "cpu_usage_percent": 25,
        "memory_usage_bytes": 1_024,
        field: value,
    }

    with pytest.raises(ValidationError):
        Observation.model_validate(values)


def test_observation_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        Observation(
            workload_id=uuid4(),
            observed_at=datetime(2026, 1, 2, 12, 0),
            state="active",
            cpu_usage_percent=10,
            memory_usage_bytes=1_024,
        )


def test_prioritize_intent_records_duration_and_preserved_workloads() -> None:
    target_id = uuid4()
    preserved_id = uuid4()

    intent = Intent(
        kind=IntentKind.PRIORITIZE,
        target_workload_id=target_id,
        preserve_workload_ids=(preserved_id,),
        duration_seconds=600,
        source_text="Prioritize my editor while keeping the build running.",
        submitted_at=NOW,
    )

    assert intent.target_workload_id == target_id
    assert intent.preserve_workload_ids == (preserved_id,)
    assert intent.duration_seconds == 600


def test_prioritize_intent_requires_duration() -> None:
    with pytest.raises(ValidationError, match="requires duration_seconds"):
        Intent(
            kind="prioritize",
            target_workload_id=uuid4(),
            source_text="Prioritize the editor.",
        )


def test_restore_intent_rejects_duration() -> None:
    with pytest.raises(ValidationError, match="cannot have duration_seconds"):
        Intent(
            kind="restore",
            target_workload_id=uuid4(),
            duration_seconds=60,
            source_text="Restore the editor.",
        )


@pytest.mark.parametrize("preserve_ids", [(uuid4(),) * 2])
def test_intent_rejects_duplicate_preserved_workloads(preserve_ids: tuple) -> None:
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        Intent(
            kind="prioritize",
            target_workload_id=uuid4(),
            preserve_workload_ids=preserve_ids,
            duration_seconds=60,
            source_text="Prioritize the editor.",
        )


def test_intent_rejects_target_as_preserved_workload() -> None:
    workload_id = uuid4()

    with pytest.raises(ValidationError, match="cannot also be a preserved workload"):
        Intent(
            kind="prioritize",
            target_workload_id=workload_id,
            preserve_workload_ids=(workload_id,),
            duration_seconds=60,
            source_text="Prioritize and preserve the editor.",
        )


def test_proposal_describes_a_bounded_expiring_cpu_change() -> None:
    proposal = Proposal(
        intent_id=uuid4(),
        workload_id=uuid4(),
        expected_cpu_weight=100,
        proposed_cpu_weight=250,
        rationale="Give the interactive workload more relative CPU under contention.",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )

    assert proposal.action.value == "set_cpu_weight"
    assert proposal.proposed_cpu_weight == 250


@pytest.mark.parametrize("weight", [0, 10_001])
def test_proposal_rejects_out_of_bounds_cpu_weight(weight: int) -> None:
    with pytest.raises(ValidationError):
        Proposal(
            intent_id=uuid4(),
            workload_id=uuid4(),
            expected_cpu_weight=100,
            proposed_cpu_weight=weight,
            rationale="Adjust CPU share.",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )


def test_proposal_requires_a_real_change_and_future_expiry() -> None:
    base = {
        "intent_id": uuid4(),
        "workload_id": uuid4(),
        "expected_cpu_weight": 100,
        "proposed_cpu_weight": 100,
        "rationale": "Adjust CPU share.",
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }

    with pytest.raises(ValidationError, match="must differ"):
        Proposal.model_validate(base)

    base["proposed_cpu_weight"] = 200
    base["expires_at"] = NOW
    with pytest.raises(ValidationError, match="must be later"):
        Proposal.model_validate(base)


def test_applied_action_result_records_actual_weights() -> None:
    result = ActionResult(
        proposal_id=uuid4(),
        workload_id=uuid4(),
        status=ActionStatus.APPLIED,
        requested_cpu_weight=250,
        previous_cpu_weight=100,
        effective_cpu_weight=250,
        reason="CPUWeight was updated and verified.",
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=20),
    )

    assert result.status is ActionStatus.APPLIED
    assert result.effective_cpu_weight == 250


def test_applied_action_result_requires_verified_weights() -> None:
    with pytest.raises(ValidationError, match="requires previous_cpu_weight"):
        ActionResult(
            proposal_id=uuid4(),
            workload_id=uuid4(),
            status="applied",
            requested_cpu_weight=250,
            reason="Reported applied without verification.",
            started_at=NOW,
            finished_at=NOW,
        )


def test_applied_action_result_rejects_unexpected_effective_weight() -> None:
    with pytest.raises(ValidationError, match="must equal requested_cpu_weight"):
        ActionResult(
            proposal_id=uuid4(),
            workload_id=uuid4(),
            status="applied",
            requested_cpu_weight=250,
            previous_cpu_weight=100,
            effective_cpu_weight=200,
            reason="Unexpected value read back.",
            started_at=NOW,
            finished_at=NOW,
        )


def test_rejected_action_result_cannot_claim_an_effective_weight() -> None:
    with pytest.raises(ValidationError, match="cannot report an effective CPU weight"):
        ActionResult(
            proposal_id=uuid4(),
            workload_id=uuid4(),
            status="rejected",
            requested_cpu_weight=250,
            previous_cpu_weight=100,
            effective_cpu_weight=250,
            reason="Target is outside the managed set.",
            started_at=NOW,
            finished_at=NOW,
        )


def test_action_result_rejects_reversed_timestamps() -> None:
    with pytest.raises(ValidationError, match="cannot be earlier"):
        ActionResult(
            proposal_id=uuid4(),
            workload_id=uuid4(),
            status="failed",
            requested_cpu_weight=250,
            reason="Executor call failed.",
            started_at=NOW,
            finished_at=NOW - timedelta(seconds=1),
        )


def test_every_contract_round_trips_through_json() -> None:
    workload = Workload(name="Editor", unit="editor.service", registered_at=NOW)
    contracts = [
        workload,
        Observation(
            workload_id=workload.workload_id,
            observed_at=NOW,
            state="active",
            cpu_usage_percent=50,
            memory_usage_bytes=2_048,
        ),
        Intent(
            kind="prioritize",
            target_workload_id=workload.workload_id,
            duration_seconds=60,
            source_text="Prioritize the editor.",
            submitted_at=NOW,
        ),
        Proposal(
            intent_id=uuid4(),
            workload_id=workload.workload_id,
            expected_cpu_weight=100,
            proposed_cpu_weight=200,
            rationale="Improve responsiveness.",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        ),
        ActionResult(
            proposal_id=uuid4(),
            workload_id=workload.workload_id,
            status="rejected",
            requested_cpu_weight=200,
            reason="Cooldown is active.",
            started_at=NOW,
            finished_at=NOW,
        ),
    ]

    for contract in contracts:
        restored = type(contract).model_validate_json(contract.model_dump_json())
        assert restored == contract
