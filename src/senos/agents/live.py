"""Read-only live context assembly. It never captures or executes policies."""

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable

from senos.contracts import Intent, IntentKind
from senos.storage.registry import RegistryStore
from senos.telemetry.status import default_telemetry
from senos.workloads.errors import RegistrationError
from .cpu import CPUContext, CPUPolicy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def collect_live_context(
    database: Path,
    target_name: str | None = None,
    *,
    intent: Intent | None = None,
    preserve_names: tuple[str, ...] = (),
    duration_seconds: int = 600,
    telemetry=None,
    pause: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], datetime] = _utc_now,
) -> tuple[CPUContext, tuple[str, ...]]:
    store = RegistryStore(database)
    try:
        records = store.list_workloads()
    except RegistrationError as error:
        raise ValueError(str(error)) from error
    workloads = tuple(record.to_workload() for record in records)
    if intent is None:
        by_name = {record.name: record for record in records}
        if target_name is None:
            raise ValueError("A registered target or validated intent is required")
        target_name = target_name.strip()
        preserve_names = tuple(name.strip() for name in preserve_names)
        for name in (target_name, *preserve_names):
            if name not in by_name:
                raise ValueError(f"Workload {name!r} is not registered")
        if target_name in preserve_names or len(set(preserve_names)) != len(preserve_names):
            raise ValueError("Preserved names must be distinct and cannot include the target")
        intent = Intent(
            kind="prioritize", target_workload_id=by_name[target_name].id,
            preserve_workload_ids=tuple(by_name[name].id for name in preserve_names),
            duration_seconds=duration_seconds,
            source_text=f"Prioritize registered workload {target_name}", submitted_at=utc_now(),
        )
    else:
        if target_name is not None or preserve_names or duration_seconds != 600:
            raise ValueError("A validated intent cannot be combined with target selection options")
        if intent.kind is not IntentKind.PRIORITIZE:
            raise ValueError("Only prioritize intents can enter the CPU evaluation path")
        identifiers = {workload.workload_id for workload in workloads}
        if any(identifier not in identifiers for identifier in (intent.target_workload_id, *intent.preserve_workload_ids)):
            raise ValueError("Intent workloads are no longer registered; resolve a new intent")
    collector = telemetry if telemetry is not None else default_telemetry()
    if collector is None:
        raise ValueError("Live CPU input requires Linux with systemd and cgroup v2; use --demo elsewhere")
    policy = CPUPolicy()
    before = {workload.workload_id: collector.read_cpu_weight(workload) for workload in workloads}
    observations = []
    # Each collector round samples CPU counters over 0.5 s. Add a spacing gap
    # so three observation endpoints span at least the agent's two-second window.
    spacing = policy.min_sample_span_seconds / (policy.sample_count - 1)
    for i in range(policy.sample_count):
        if i:
            pause(spacing)
        observations.extend(collector.observe_all(workloads))
    after = {workload.workload_id: collector.read_cpu_weight(workload) for workload in workloads}
    try:
        if store.list_workloads() != records:
            raise ValueError("Registered inventory changed during sampling; retry with a fresh snapshot")
    except RegistrationError as error:
        raise ValueError(str(error)) from error
    target_weight = after[intent.target_workload_id]
    target_workload = next(workload for workload in workloads if workload.workload_id == intent.target_workload_id)
    weights = {}
    eligible_peers = []
    warnings = []
    unstable = set()
    for workload in workloads:
        identifier = workload.workload_id
        start, end = before[identifier], after[identifier]
        stable = start == end and end.invocation_id is not None and end.cgroup_dir is not None and end.weight is not None
        if not stable:
            unstable.add(identifier)
            weights[identifier] = None
            warnings.append(f"{workload.name}: weight or unit identity changed or is unavailable; evidence discarded")
            continue
        comparable = (
            workload.manager == target_workload.manager and target_weight.cgroup_dir is not None
            and end.cgroup_dir.parent == target_weight.cgroup_dir.parent
        )
        weights[identifier] = end.weight
        if comparable and identifier != intent.target_workload_id:
            eligible_peers.append(identifier)
        if not comparable:
            warnings.append(f"{workload.name}: excluded as a CPU-weight peer because manager or cgroup parent differs")
    observations = tuple(sample for sample in observations if sample.workload_id not in unstable)
    return CPUContext(intent=intent, workloads=workloads, observations=observations, current_cpu_weights=weights, eligible_cpu_peer_ids=tuple(eligible_peers)), tuple(warnings)
