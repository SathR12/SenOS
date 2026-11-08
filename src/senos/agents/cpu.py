"""Deterministic CPU proposal agent. No OS access, model calls, or execution."""

from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from senos.contracts import Intent, IntentKind, Observation, Proposal, Workload, WorkloadState
from senos.contracts._base import ContractModel, CpuWeight


class CPUContext(ContractModel):
    """Snapshot supplied by the harness, independent of the telemetry collector."""

    intent: Intent
    workloads: tuple[Workload, ...]
    observations: tuple[Observation, ...]
    current_cpu_weights: dict[UUID, CpuWeight | None]
    last_change_at: AwareDatetime | None = None
    eligible_cpu_peer_ids: tuple[UUID, ...] | None = None

    @model_validator(mode="after")
    def unique_workloads(self):
        ids = [workload.workload_id for workload in self.workloads]
        if len(ids) != len(set(ids)):
            raise ValueError("Workload IDs must be unique")
        return self


class CPUPolicy(ContractModel):
    """Experimental tunable defaults, not benchmarked performance guarantees."""

    pressure_threshold: float = Field(default=5.0, gt=0, le=100)
    memory_pressure_threshold: float = Field(default=10.0, gt=0, le=100)
    sample_count: int = Field(default=3, strict=True, ge=2, le=60)
    min_sample_span_seconds: float = Field(default=2.0, gt=0)
    max_sample_age_seconds: float = Field(default=10.0, gt=0)
    cooldown_seconds: float = Field(default=30.0, gt=0)
    boost_factor: float = Field(default=2.0, gt=1, le=2)
    max_weight: CpuWeight = 1000


class CPUDecision(ContractModel):
    status: Literal["proposed", "no_action"]
    reason: str
    proposal: Proposal | None = None

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "proposed") != (self.proposal is not None):
            raise ValueError("Only a proposed decision can contain a proposal")
        return self


def no_action(reason: str) -> CPUDecision:
    return CPUDecision(status="no_action", reason=reason)


class CPUAgent:
    def __init__(self, policy: CPUPolicy | None = None):
        self.policy = policy if policy is not None else CPUPolicy()

    def evaluate(self, context: CPUContext, *, now: datetime | None = None) -> CPUDecision:
        now = now if now is not None else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Decision time must include a timezone")
        intent, policy = context.intent, self.policy
        if intent.kind is not IntentKind.PRIORITIZE:
            return no_action("Restore intents belong to recovery, not the CPU proposal agent")
        if intent.submitted_at > now:
            return no_action("Intent is future-dated")
        expires = intent.submitted_at + timedelta(seconds=intent.duration_seconds)
        if expires <= now:
            return no_action("Intent is expired")
        if context.last_change_at is not None:
            elapsed = (now - context.last_change_at).total_seconds()
            if elapsed < policy.cooldown_seconds:
                return no_action("A recent or future-dated change prevents another proposal during cooldown")
        workloads = {workload.workload_id: workload for workload in context.workloads}
        target = intent.target_workload_id
        if target not in workloads or any(identifier not in workloads for identifier in intent.preserve_workload_ids):
            return no_action("Target and preserved workloads must be present in the registered inventory")
        current = context.current_cpu_weights.get(target)
        if current is None:
            return no_action("Target CPU weight is unknown; do not guess a baseline")
        if current >= policy.max_weight:
            return no_action("Target is already at or above the configured CPU-weight ceiling")

        def fresh(sample: Observation) -> bool:
            return 0 <= (now - sample.observed_at).total_seconds() <= policy.max_sample_age_seconds

        history = sorted((sample for sample in context.observations if sample.workload_id == target), key=lambda sample: sample.observed_at)
        samples = history[-policy.sample_count:]
        if len(samples) < policy.sample_count:
            return no_action("Not enough target observations to establish sustained contention")
        if any(not fresh(sample) or sample.state is not WorkloadState.ACTIVE or sample.cpu_usage_percent is None or sample.cpu_usage_percent <= 0 for sample in samples):
            return no_action("Target samples must be fresh, active, and include measured CPU use")
        if len({sample.observed_at for sample in samples}) != len(samples) or (samples[-1].observed_at - samples[0].observed_at).total_seconds() < policy.min_sample_span_seconds:
            return no_action("Target samples must cover distinct times over a sufficient observation window")
        if any(sample.cpu_pressure_some_avg10 is None or sample.cpu_pressure_some_avg10 < policy.pressure_threshold for sample in samples):
            return no_action("Sustained CPU pressure is missing; utilization alone does not justify a change")
        if any(sample.memory_pressure_some_avg10 is not None and sample.memory_pressure_some_avg10 >= policy.memory_pressure_threshold for sample in samples):
            return no_action("Memory pressure is elevated; a CPU-only recommendation may be inappropriate")
        latest = {}
        for sample in sorted(context.observations, key=lambda sample: sample.observed_at):
            latest[sample.workload_id] = sample
        for identifier in intent.preserve_workload_ids:
            sample = latest.get(identifier)
            if sample is None or not fresh(sample) or sample.state is not WorkloadState.ACTIVE or sample.cpu_usage_percent is None:
                return no_action("A preserved workload lacks a fresh active observation")
        competing = [identifier for identifier, sample in latest.items()
            if identifier in workloads and identifier != target
            and (context.eligible_cpu_peer_ids is None or identifier in context.eligible_cpu_peer_ids)
            and workloads[identifier].manager == workloads[target].manager
            and context.current_cpu_weights.get(identifier) is not None
            and fresh(sample) and sample.state is WorkloadState.ACTIVE
            and sample.cpu_usage_percent is not None and sample.cpu_usage_percent > 0]
        if not competing:
            return no_action("No eligible active managed CPU peer is observed for the target scheduling group")
        proposed = min(policy.max_weight, int(current * policy.boost_factor))
        if proposed <= current:
            return no_action("No bounded CPU-weight increase is available")
        reason = "Sustained target CPU pressure and an active managed peer support a bounded target-weight boost; peer weights remain unchanged"
        return CPUDecision(status="proposed", reason=reason, proposal=Proposal(
            intent_id=intent.intent_id, workload_id=target,
            expected_cpu_weight=current, proposed_cpu_weight=proposed,
            rationale=reason, created_at=now, expires_at=expires,
        ))
