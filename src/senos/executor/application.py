"""Restricted, explicitly authorized CPU application with independent recovery."""

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from senos.agents.cpu import CPUAgent, CPUContext
from senos.contracts import ActionResult, Intent, Workload
from senos.executor.cpu import SystemdCPUBackend
from senos.executor.locking import cpu_lock
from senos.recovery.errors import RestoreError
from senos.recovery.restore import RestoreService
from senos.storage.changes import CPUJournal
from senos.storage.execution import ExecutionStore
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.registry import Registry


def utc_now():
    return datetime.now(timezone.utc)


def process_identity(pid):
    try:
        # stat command names may contain spaces and parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def arm_recovery(database, session_id, seconds):
    unit = "senos-recovery-" + session_id.replace("-", "")
    try:
        subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", "--property=Type=exec",
            "--property=Restart=on-failure", "--property=RestartSec=1", f"--property=RuntimeMaxSec={math.ceil(seconds) + 120}",
            sys.executable, "-m", "senos.recovery.expiration", "--db", str(database.resolve()), "--session", session_id],
            check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise RestoreError("Cannot arm independent recovery; no CPU policy will be applied") from error
    store = ExecutionStore(database)
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if store.get(session_id)["recovery_ready"]:
            return
        time.sleep(0.1)
    raise RestoreError("Recovery worker did not acknowledge readiness; no CPU policy will be applied")


class CPUApplication:
    def __init__(self, database: Path, *, backend=None, telemetry=None, recovery=arm_recovery, now=utc_now):
        self.database = database
        self.backend = backend if backend is not None else SystemdCPUBackend()
        self.telemetry = telemetry if telemetry is not None else LinuxTelemetry()
        self.recovery = recovery
        self.now = now
        self.store = ExecutionStore(database)
        self.journal = CPUJournal(database)

    def begin(self, intent: Intent) -> str:
        if sys.platform != "linux":
            raise RestoreError("CPU application requires Linux/systemd")
        deadline = intent.submitted_at + timedelta(seconds=intent.duration_seconds or 0)
        if intent.kind.value != "prioritize" or (deadline - self.now()).total_seconds() < 5:
            raise RestoreError("Prioritize intent needs at least five seconds remaining before application")
        with cpu_lock(self.database):
            registry = Registry(self.database)
            workload = registry.get_by_id(intent.target_workload_id)
            for identifier in intent.preserve_workload_ids:
                registry.get_by_id(identifier)
            if workload.manager != "user":
                raise RestoreError("Initial CPU application supports user-manager workloads only")
            if any(point["workload_id"] == str(workload.workload_id) for point in self.journal.pending()):
                raise RestoreError("Workload has pending recovery; restore it before starting another objective")
            state = self.backend.read(workload)
            effective = self.telemetry.read_cpu_weight(workload)
            if effective.weight is None or effective.invocation_id != state.invocation_id or str(effective.cgroup_dir) != "/sys/fs/cgroup" + state.cgroup:
                raise RestoreError("Cannot verify effective CPU weight and invocation identity")
            owner_start = process_identity(os.getpid())
            if not owner_start:
                raise RestoreError("Cannot establish controller process identity")
            session_id = str(uuid4())
            self.store.create((session_id, str(workload.workload_id), intent.model_dump_json(), workload.model_dump_json(),
                state.invocation_id, state.cgroup, effective.weight, deadline.isoformat(), "active", os.getpid(), owner_start))
            try:
                if self.backend.read(workload) != state:
                    raise RestoreError("CPU configuration changed before original-setting capture")
                self.journal.capture(workload, state)
                self.recovery(self.database, session_id, (deadline - self.now()).total_seconds())
            except Exception:
                if not any(point["workload_id"] == str(workload.workload_id) for point in self.journal.pending()):
                    self.store.end_without_change(session_id)
                else:
                    self.finish(session_id)
                raise
            return session_id

    def apply(self, session_id: str, evaluation: dict) -> ActionResult | None:
        context = CPUContext.model_validate(evaluation["context"])
        decision = CPUAgent().evaluate(context, now=self.now())
        if decision.proposal is None:
            return None
        proposal = decision.proposal
        started = self.now()
        previous = None
        with cpu_lock(self.database):
            session = self.store.get(session_id)
            def result(status, reason, effective=None):
                outcome = ActionResult(proposal_id=proposal.proposal_id, workload_id=proposal.workload_id,
                    status=status, requested_cpu_weight=proposal.proposed_cpu_weight,
                    previous_cpu_weight=previous, effective_cpu_weight=effective,
                    reason=reason, started_at=started, finished_at=max(started, self.now()))
                self.store.record(session_id, outcome)
                return outcome
            try:
                if session["status"] != "active" or not session["recovery_ready"]:
                    return result("rejected", "Execution ownership ended or independent recovery is not ready")
                if session["owner_pid"] != os.getpid() or session["owner_start"] != process_identity(os.getpid()):
                    return result("rejected", "Execution session belongs to a different controller process")
                intent = Intent.model_validate_json(session["intent_json"])
                if context.intent != intent or proposal.intent_id != intent.intent_id or proposal.workload_id != intent.target_workload_id:
                    return result("rejected", "Evaluation does not match the authorized objective")
                if intent.submitted_at > started or started >= datetime.fromisoformat(session["deadline"]) or proposal.expires_at > datetime.fromisoformat(session["deadline"]):
                    return result("rejected", "Intent or proposal is expired or future-dated")
                if session["last_change_at"] and (started - datetime.fromisoformat(session["last_change_at"])).total_seconds() < 30:
                    return result("rejected", "A real CPU change is within its persistent 30-second cooldown")
                ceiling = min(1000, session["original_effective"] * 2)
                if not proposal.expected_cpu_weight < proposal.proposed_cpu_weight <= ceiling:
                    return result("rejected", "Proposal exceeds the original-weight 2x ceiling; repeated boosts are not allowed")
                workload = Registry(self.database).get_by_id(intent.target_workload_id)
                if workload != Workload.model_validate_json(session["workload_json"]):
                    return result("rejected", "Registered target changed")
                state = self.backend.read(workload)
                measured = self.telemetry.read_cpu_weight(workload)
                previous = measured.weight
                if state.invocation_id != session["invocation_id"] or state.cgroup != session["cgroup"] or measured.invocation_id != state.invocation_id or str(measured.cgroup_dir) != "/sys/fs/cgroup" + state.cgroup:
                    return result("rejected", "Unit invocation or cgroup changed; refusing to affect a different instance")
                if previous != proposal.expected_cpu_weight:
                    return result("rejected", "Effective CPU weight changed since observation")
                if session["last_applied"] is not None and previous != session["last_applied"]:
                    return result("rejected", "CPU settings were changed outside this session")
                point = next((point for point in self.journal.pending() if point["workload_id"] == str(workload.workload_id)), None)
                if point is None or point["invocation_id"] != state.invocation_id or point["cgroup"] != state.cgroup:
                    return result("rejected", "Original-setting recovery record is missing or mismatched")
                # Recheck immediately before the restricted write, after all potentially slow reads.
                if self.now() >= proposal.expires_at:
                    return result("rejected", "Proposal expired during validation")
                inventory = Registry(self.database).list_workloads()
                if tuple(inventory) != context.workloads:
                    return result("rejected", "Registered inventory changed since observation")
                latest = {sample.workload_id: sample for sample in context.observations}
                peers = []
                for peer in inventory:
                    if peer.workload_id == workload.workload_id:
                        continue
                    if peer.workload_id in intent.preserve_workload_ids:
                        self.backend.read(peer)
                    sample = latest.get(peer.workload_id)
                    if sample is None or not sample.cpu_usage_percent or peer.manager != workload.manager:
                        continue
                    live_peer = self.telemetry.read_cpu_weight(peer)
                    if live_peer.weight is not None and live_peer.cgroup_dir is not None and live_peer.cgroup_dir.parent == measured.cgroup_dir.parent:
                        peers.append(peer)
                if not peers:
                    return result("rejected", "No live comparable CPU peer remains")
                verified = self.backend.read(workload)
                if verified != state:
                    return result("rejected", "Unit state changed during authorization")
                if self.now() >= proposal.expires_at:
                    return result("rejected", "Proposal expired immediately before the write")
                self.store.plan_write(session_id, proposal.proposed_cpu_weight, self.now())
                self.backend.write(workload, proposal.proposed_cpu_weight)
                verified = self.backend.read(workload)
                effective = self.telemetry.read_cpu_weight(workload)
                if verified.invocation_id != state.invocation_id or verified.cgroup != state.cgroup or verified.weight != proposal.proposed_cpu_weight or effective.weight != proposal.proposed_cpu_weight or effective.invocation_id != state.invocation_id:
                    raise RestoreError("CPU write could not be verified; original settings require recovery")
                return result("applied", "Bounded CPUWeight applied and verified; independent recovery is armed", effective.weight)
            except Exception as error:
                self.finish(session_id)
                return result("failed", f"Application failed and recovery was attempted: {error}"[:1000])

    def finish(self, session_id: str) -> list[dict]:
        with cpu_lock(self.database):
            session = self.store.get(session_id)
            if session["status"] == "restored":
                return []
            intent = Intent.model_validate_json(session["intent_json"])
            target = Registry(self.database).get_by_id(intent.target_workload_id)
            pending = [point for point in self.journal.pending() if point["workload_id"] == str(target.workload_id)]
            if not pending:
                if session["last_applied"] is not None:
                    raise RestoreError("Recovery point missing after an applied change; refusing to guess original settings")
                self.store.end_without_change(session_id)
                return []
            point = pending[0]
            current = self.backend.read(target)
            expected = session["last_applied"] if session["last_applied"] is not None else point["original_weight"]
            if current.weight not in {expected, point["original_weight"]}:
                raise RestoreError("CPU settings changed outside SenOS; refusing automatic overwrite. Review and run senos restore explicitly")
            outcomes = RestoreService(self.database, self.backend).restore(target.name)
            from dataclasses import asdict
            if any(outcome.status == "failed" for outcome in outcomes):
                raise RestoreError("Original CPU recovery failed; journal remains pending. Run senos restore and inspect the recovery worker")
            return [asdict(outcome) for outcome in outcomes]
