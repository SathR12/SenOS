"""Shared validated data contracts for SenOS components."""

from .action_result import ActionResult, ActionStatus
from .intent import Intent, IntentKind
from .observation import Observation, WorkloadState
from .proposal import Proposal, ProposalAction
from .workload import Workload

__all__ = [
    "ActionResult",
    "ActionStatus",
    "Intent",
    "IntentKind",
    "Observation",
    "Proposal",
    "ProposalAction",
    "Workload",
    "WorkloadState",
]
