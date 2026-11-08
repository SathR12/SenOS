"""Deterministic validation of persisted CPU proposals."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from senos.agents.cpu import CPUPolicy
from senos.contracts import Intent, Proposal, ProposalAction, Workload
from senos.executor.cpu import CPUState


@dataclass(frozen=True)
class ValidationDecision:
    """The authorization result that must precede every executor call."""

    proposal_id: UUID
    accepted: bool
    reason: str


class ProposalValidator:
    """Validate a proposal against current allowlist and unit evidence."""

    def __init__(self, policy: CPUPolicy | None = None):
        self.policy = policy if policy is not None else CPUPolicy()

    def validate(
        self,
        proposal: Proposal,
        intent: Intent,
        workload: Workload | None,
        current: CPUState | None,
        *,
        preserved_workload_ids: tuple[UUID, ...] = (),
        now: datetime | None = None,
        cooldown_elapsed: bool = True,
        executor_allowed: bool = True,
    ) -> ValidationDecision:
        now = now if now is not None else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Validation time must include a timezone")
        def reject(reason: str) -> ValidationDecision:
            return ValidationDecision(proposal.proposal_id, False, reason)
        if proposal.action is not ProposalAction.SET_CPU_WEIGHT:
            return reject("Operation is not allowlisted")
        if proposal.intent_id != intent.intent_id:
            return reject("Proposal does not belong to the persisted intent")
        if workload is None or workload.workload_id != proposal.workload_id:
            return reject("Workload is not currently registered")
        if proposal.workload_id in preserved_workload_ids:
            return reject("Preserved workloads cannot be changed")
        if proposal.expires_at <= now:
            return reject("Proposal has expired")
        if not cooldown_elapsed:
            return reject("Workload is in cooldown")
        if not executor_allowed:
            return reject("Executor is not permitted to apply this operation")
        if current is None or current.weight is None:
            return reject("Current CPU weight is unavailable")
        if current.weight != proposal.expected_cpu_weight:
            return reject("Current CPU weight differs from the proposal expectation")
        if not 1 <= proposal.proposed_cpu_weight <= self.policy.max_weight:
            return reject("Proposed CPU weight is outside configured bounds")
        if proposal.proposed_cpu_weight == current.weight:
            return reject("Proposal does not change the current CPU weight")
        if not current.invocation_id or not current.cgroup:
            return reject("Current unit identity is unavailable")
        return ValidationDecision(proposal.proposal_id, True, "Proposal validated")
