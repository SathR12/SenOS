"""Restricted proposal execution with durable recovery and read-back."""

from datetime import datetime, timezone
from typing import Protocol

from senos.contracts import ActionResult, ActionStatus, Proposal, Workload
from senos.policy.service import ValidationDecision
from senos.recovery.errors import RestoreError
from senos.storage.persistence import PolicyStore
from .cpu import CPUState


class CPUWriter(Protocol):
    def read(self, workload: Workload) -> CPUState: ...
    def write(self, workload: Workload, weight: int | None) -> None: ...


class ProposalExecutor:
    """Apply only an accepted CPU proposal through structured backend calls."""

    def __init__(self, database, backend: CPUWriter):
        self.store = PolicyStore(database)
        self.backend = backend

    def execute(
        self,
        proposal: Proposal,
        workload: Workload,
        validation: ValidationDecision,
        *,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> ActionResult:
        started = now or datetime.now(timezone.utc)
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError("Execution time must include a timezone")
        self.store.save_validation(validation, decided_at=started)
        if not validation.accepted:
            result = ActionResult(
                proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
                status=ActionStatus.REJECTED,
                requested_cpu_weight=proposal.proposed_cpu_weight,
                reason=validation.reason, started_at=started, finished_at=started,
            )
            return self.store.save_action_result(result)
        current = self.backend.read(workload)
        if current.weight != proposal.expected_cpu_weight or not current.invocation_id or not current.cgroup:
            result = ActionResult(
                proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
                status=ActionStatus.REJECTED,
                requested_cpu_weight=proposal.proposed_cpu_weight,
                previous_cpu_weight=current.weight,
                reason="Unit identity or expected CPU weight changed before execution",
                started_at=started,
            )
            return self.store.save_action_result(result)
        if dry_run:
            result = ActionResult(
                proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
                status=ActionStatus.REJECTED,
                requested_cpu_weight=proposal.proposed_cpu_weight,
                previous_cpu_weight=current.weight,
                reason="Dry-run mode; no resource setting was changed",
                started_at=started, finished_at=started,
            )
            return self.store.save_action_result(result)
        self.store.save_recovery(
            proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
            previous_configured_weight=current.weight,
            current_effective_weight=current.weight,
            invocation_id=current.invocation_id, cgroup_identity=current.cgroup,
            expires_at=proposal.expires_at,
        )
        try:
            self.backend.write(workload, proposal.proposed_cpu_weight)
            verified = self.backend.read(workload)
            if (
                verified.invocation_id != current.invocation_id
                or verified.cgroup != current.cgroup
                or verified.weight != proposal.proposed_cpu_weight
            ):
                raise RuntimeError("CPU weight read-back did not match the requested value")
        except (OSError, RestoreError, RuntimeError) as error:
            recovery_error = None
            try:
                recovery_state = self.backend.read(workload)
                if (
                    recovery_state.invocation_id == current.invocation_id
                    and recovery_state.cgroup == current.cgroup
                    and recovery_state.weight != current.weight
                ):
                    self.backend.write(workload, current.weight)
            except (OSError, RestoreError) as restore_error:
                recovery_error = restore_error
            reason = str(error)
            if recovery_error is not None:
                reason += f"; recovery failed: {recovery_error}"
            result = ActionResult(
                proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
                status=ActionStatus.FAILED,
                requested_cpu_weight=proposal.proposed_cpu_weight,
                previous_cpu_weight=current.weight,
                reason=reason, started_at=started,
            )
            return self.store.save_action_result(result)
        result = ActionResult(
            proposal_id=proposal.proposal_id, workload_id=workload.workload_id,
            status=ActionStatus.APPLIED,
            requested_cpu_weight=proposal.proposed_cpu_weight,
            previous_cpu_weight=current.weight,
            effective_cpu_weight=verified.weight,
            reason="CPU weight applied and verified",
            started_at=started,
        )
        return self.store.save_action_result(result)
