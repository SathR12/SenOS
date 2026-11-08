"""CPU dry-run harness: synthetic, file, or read-only live inputs."""

from datetime import datetime, timezone
from pathlib import Path

from .cpu import CPUAgent, CPUContext
from .demo import cpu_context
from .live import collect_live_context
from senos.contracts import ExecutionSnapshot
from senos.storage.persistence import PolicyStore


def run_cpu(*, input_path: Path | None = None, target_name: str | None = None, database: Path | None = None, preserve_names: tuple[str, ...] = (), duration_seconds: int = 600) -> dict:
    if target_name is None and (preserve_names or duration_seconds != 600):
        raise ValueError("Live intent options require a target workload")
    warnings = ()
    if target_name is not None:
        if database is None or input_path is not None:
            raise ValueError("Live input requires a registry path and cannot be combined with a file")
        context, warnings = collect_live_context(database, target_name, preserve_names=preserve_names, duration_seconds=duration_seconds)
        source = "live"
    elif input_path is not None:
        context = CPUContext.model_validate_json(input_path.read_text(encoding="utf-8"))
        source = "file"
    else:
        context = cpu_context()
        source = "demo"
    # Decide after collection, so newly collected timestamps are not in the future.
    now = datetime.now(timezone.utc)
    store = PolicyStore(database) if source == "live" else None
    if store is not None:
        store.save_intent(context.intent)
        context = context.model_copy(update={"intent": store.get_intent(context.intent.intent_id)})
        observations = tuple(
            observation.model_copy(update={"intent_id": context.intent.intent_id})
            for observation in context.observations
        )
        context = context.model_copy(update={"observations": observations})
        observed_ids = {observation.workload_id for observation in context.observations}
        store.save_snapshot(ExecutionSnapshot(
            intent_id=context.intent.intent_id,
            observations=context.observations,
            cpu_weights={identifier: context.current_cpu_weights[identifier] for identifier in observed_ids},
            invocation_ids={
                identifier: next(sample.invocation_id for sample in context.observations
                                 if sample.workload_id == identifier)
                for identifier in observed_ids
            },
            cgroup_identities={
                identifier: next(sample.cgroup_identity for sample in context.observations
                                 if sample.workload_id == identifier)
                for identifier in observed_ids
            },
        ))
    agent = CPUAgent()
    decision = agent.evaluate(context, now=now)
    if store is not None:
        if decision.proposal is not None:
            store.save_proposal(decision.proposal)
        else:
            store.save_no_action(context.intent, decision.reason, now=now)
    return {
        "mode": "dry_run", "source": source,
        "context": context.model_dump(mode="json"), "warnings": list(warnings),
        "decision": decision.model_dump(mode="json"),
    }
