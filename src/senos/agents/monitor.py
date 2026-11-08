"""Foreground monitoring loop. Application requires explicit authorization."""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import time
from typing import Callable

from senos.contracts import Intent, IntentKind
from senos.intent import IntentError
from senos.workloads.errors import RegistrationError
from senos.intent.pipeline import evaluate_intent
from senos.storage.monitor import MonitorError, MonitorStore


def utc_now():
    return datetime.now(timezone.utc)


def monitor_intent(
    intent: Intent, database: Path, log_database: Path, *, interval: float = 5,
    max_cycles: int | None = None, initial_evaluation: dict | None = None,
    apply: bool = False, application=None,
    on_event: Callable[[dict], None] | None = None, on_start: Callable[[str], None] | None = None,
    evaluate: Callable = evaluate_intent, pause: Callable = time.sleep,
    now: Callable = utc_now, clock: Callable = time.monotonic,
) -> dict:
    if intent.kind is not IntentKind.PRIORITIZE:
        raise MonitorError("Only prioritize intents can be monitored")
    if not math.isfinite(interval) or not 1 <= interval <= 60:
        raise MonitorError("Monitor interval must be finite and from 1 through 60 seconds")
    if max_cycles is not None and (type(max_cycles) is not int or not 1 <= max_cycles <= 10000):
        raise MonitorError("Cycle limit must be an integer from 1 through 10000")
    if database.resolve() == log_database.resolve() or (database.exists() and log_database.exists() and database.samefile(log_database)):
        raise MonitorError("Monitor database must be separate from the workload registry")
    started = now()
    if intent.submitted_at > started:
        raise MonitorError("Cannot monitor a future-dated intent")
    deadline = intent.submitted_at + timedelta(seconds=intent.duration_seconds)
    # A wall-clock rollback must never extend the original remaining lifetime.
    monotonic_deadline = clock() + max(0, (deadline - started).total_seconds())
    store = MonitorStore(log_database)
    run_id = store.create(intent, database, deadline)
    cycles = 0
    execution_session = None
    applied_any = False
    recovery_outcomes = []
    status, reason = "expired", "Original intent deadline reached; no resource policy was applied"

    def remaining():
        return min((deadline - now()).total_seconds(), monotonic_deadline - clock())

    try:
        if apply:
            from senos.executor.application import CPUApplication
            application = application if application is not None else CPUApplication(database)
            execution_session = application.begin(intent)
            initial_evaluation = None  # Collect fresh evidence after binding the invocation.
        if on_start:
            on_start(run_id)
        while remaining() > 0:
            if store.get(run_id)["status"] != "running":
                break
            if cycles >= (max_cycles or 10000):
                status, reason = "limited", "Cycle limit reached; monitoring ended without applying policies"
                break
            evaluation = initial_evaluation if cycles == 0 and initial_evaluation is not None else evaluate(intent, database)
            if evaluation.get("executed") is not False or evaluation.get("mode") != "dry_run" or evaluation.get("intent") != intent.model_dump(mode="json"):
                raise MonitorError("Monitor received an invalid evaluation or a different intent")
            # Expiration and explicit stop take precedence over publishing a recommendation.
            if remaining() <= 0:
                break
            if apply:
                # A concurrent stop must win before the executor gets authorization.
                if store.get(run_id)["status"] != "running":
                    break
                action = application.apply(execution_session, evaluation)
                evaluation = {**evaluation, "mode": "apply", "executed": action is not None and action.status.value == "applied",
                    "execution": action.model_dump(mode="json") if action is not None else None}
                applied_any = applied_any or evaluation["executed"]
                if action is not None and action.status.value == "failed":
                    store.append(run_id, evaluation)
                    raise MonitorError(action.reason)
            if not store.append(run_id, evaluation):
                break
            cycles += 1
            if on_event:
                on_event({"run_id": run_id, "cycle": cycles, "evaluation": evaluation})
            if cycles >= (max_cycles or 10000):
                status, reason = "limited", "Cycle limit reached; monitoring ended without applying policies"
                break
            until = clock() + min(interval, max(0, remaining()))
            while clock() < until and remaining() > 0:
                if store.get(run_id)["status"] != "running":
                    break
                delay = max(0, min(1, until - clock(), remaining()))
                if delay:
                    pause(delay)
        store.finish(run_id, status, reason if not apply else reason.replace("without applying policies", "and original settings will be restored").replace("no resource policy was applied", "application session ended"))
    except KeyboardInterrupt:
        store.finish(run_id, "interrupted", "Stopped with Ctrl+C; restoration is required if CPU policies were applied")
    except Exception as error:
        store.finish(run_id, "failed", "Monitoring stopped after a collection or persistence error")
        from senos.recovery.errors import RestoreError
        if isinstance(error, (IntentError, MonitorError, RestoreError, RegistrationError, OSError, ValueError)):
            raise MonitorError(f"Monitoring failed (run {run_id}): {error}") from error
        raise
    finally:
        if execution_session is not None:
            try:
                recovery_outcomes = application.finish(execution_session)
            except Exception as error:
                store.finish(run_id, "failed", "CPU restoration failed; recovery journal remains pending", force=True)
                raise MonitorError(f"CPU restoration failed (run {run_id}): {error}. Run senos restore.") from error
    result = store.get(run_id)
    return {"mode": "apply" if apply else "dry_run", "executed": applied_any, "recovery": recovery_outcomes, "execution_session": execution_session, "monitor_database": str(log_database.resolve()), **result}
