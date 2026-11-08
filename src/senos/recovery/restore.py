"""Restore only CPUWeight values actually captured for registered workloads."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from senos.contracts import Workload

from senos.executor.cpu import CPUState, SystemdCPUBackend
from senos.storage.changes import CPUJournal
from senos.storage.persistence import PersistenceError, PolicyStore
from senos.storage.registry import RegisteredWorkload, RegistryStore
from senos.workloads.errors import RegistrationError
from .errors import RestoreError


@dataclass(frozen=True)
class RestoreOutcome:
    name: str
    status: str
    original_cpu_weight: int | None
    message: str


class CPUBackend(Protocol):
    def read(self, workload: Workload) -> CPUState: ...
    def write(self, workload: Workload, weight: int | None) -> None: ...


def _contract(record: RegisteredWorkload) -> Workload:
    try:
        return record.to_workload()
    except ValueError as error:
        raise RestoreError("Invalid saved workload; refusing resource operations") from error


class RestoreService:
    def __init__(self, database: Path, backend: CPUBackend | None = None):
        self.database = database
        self.store = RegistryStore(database)
        self.journal = CPUJournal(database)
        self.policy_store = PolicyStore(database)
        self.backend = backend if backend is not None else SystemdCPUBackend()

    def _workloads(self) -> list[RegisteredWorkload]:
        try:
            return self.store.list_workloads()
        except RegistrationError as error:
            raise RestoreError(str(error)) from error

    def capture(self, name: str) -> None:
        """Future executor hook: persist original BEFORE making any change.

        This records recovery data only; it does not apply a CPU policy.
        """
        record = next((item for item in self._workloads() if item.name == name), None)
        if record is None:
            raise RestoreError(f"Workload {name!r} is not registered")
        workload = _contract(record)
        self.journal.capture(workload, self.backend.read(workload))

    def _check(self, workload, point):
        state = self.backend.read(workload)
        if state.invocation_id != point["invocation_id"] or state.cgroup != point["cgroup"]:
            raise RestoreError("Unit restarted or changed cgroup; refusing to modify a different invocation")
        return state

    def restore(self, name: str | None = None, *, dry_run: bool = False) -> list[RestoreOutcome]:
        records = self._workloads()
        if name is not None:
            name = name.strip()
            records = [item for item in records if item.name == name]
            if not records:
                raise RestoreError(f"Workload {name!r} is not registered")
        by_id = {item.id: item for item in records}
        results = []
        for point in self.journal.pending():
            if name is not None and point["workload_id"] not in by_id:
                continue
            record = by_id.get(point["workload_id"])
            if record is None:
                results.append(RestoreOutcome(point["workload_id"], "failed", point["original_weight"], "Recorded workload is no longer registered"))
                continue
            try:
                workload = _contract(record)
            except RestoreError as error:
                results.append(RestoreOutcome(record.name, "failed", point["original_weight"], str(error)))
                continue

            def operation(saved):
                if saved["unit"] != workload.unit or saved["manager"] != workload.manager:
                    raise RestoreError("Registered target differs from the recorded original")
                state = self._check(workload, saved)
                if not dry_run and state.weight != saved["original_weight"]:
                    self.backend.write(workload, saved["original_weight"])
                    verified = self._check(workload, saved)
                    if verified.weight != saved["original_weight"]:
                        raise RestoreError("CPUWeight verification failed; recovery remains pending")
            try:
                if dry_run:
                    operation(point)
                    message = None
                else:
                    message = self.journal.finish(point["workload_id"], point["captured_at"], operation)
                status = "failed" if message else ("dry_run" if dry_run else "restored")
                results.append(RestoreOutcome(record.name, status, point["original_weight"], message or ("Would restore recorded CPUWeight" if dry_run else "Original CPUWeight verified")))
            except RestoreError as error:
                results.append(RestoreOutcome(record.name, "failed", point["original_weight"], str(error)))
        return results

    def reconcile(
        self, name: str | None = None, *, now=None, dry_run: bool = False, force: bool = False
    ) -> list[RestoreOutcome]:
        """Recover durable proposal actions after a crash, expiry, or request."""
        if not self.database.exists():
            return []
        now = now or datetime.now(timezone.utc)
        try:
            points = self.policy_store.pending_recovery()
        except PersistenceError as error:
            raise RestoreError(str(error)) from error
        records = {item.id: item for item in self._workloads()}
        selected_ids = {
            item.id for item in records.values() if name is None or item.name == name
        }
        results = []
        for point in points:
            if point["workload_id"] not in selected_ids:
                continue
            record = records.get(point["workload_id"])
            if record is None:
                self.policy_store.set_recovery_state(point["proposal_id"], "intervention_required")
                results.append(RestoreOutcome(point["workload_id"], "failed", point["previous_configured_weight"], "Workload is no longer registered"))
                continue
            workload = _contract(record)
            try:
                state = self.backend.read(workload)
                if state.invocation_id != point["invocation_id"] or state.cgroup != point["cgroup_identity"]:
                    self.policy_store.set_recovery_state(point["proposal_id"], "intervention_required")
                    results.append(RestoreOutcome(record.name, "failed", point["previous_configured_weight"], "Unit identity changed; intervention required"))
                    continue
                expires = datetime.fromisoformat(point["expires_at"])
                if point["state"] in {"pending_apply", "applying"}:
                    if state.weight == point["current_effective_weight"]:
                        self.policy_store.set_recovery_state(point["proposal_id"], "intervention_required")
                        results.append(RestoreOutcome(record.name, "failed", point["previous_configured_weight"], "Action did not reach a verified applied state"))
                        continue
                if (
                    point["state"] in {"applied", "pending_restore", "restore_failed"}
                    and not force and expires > now
                ):
                    continue
                if dry_run:
                    results.append(RestoreOutcome(record.name, "dry_run", point["previous_configured_weight"], "Would restore durable recovery state"))
                    continue
                if state.weight == point["previous_configured_weight"]:
                    self.policy_store.set_recovery_state(point["proposal_id"], "restored")
                    results.append(RestoreOutcome(record.name, "restored", point["previous_configured_weight"], "Previous CPUWeight was already effective"))
                    continue
                self.policy_store.set_recovery_state(point["proposal_id"], "pending_restore")
                self.backend.write(workload, point["previous_configured_weight"])
                verified = self.backend.read(workload)
                if verified.invocation_id != point["invocation_id"] or verified.cgroup != point["cgroup_identity"] or verified.weight != point["previous_configured_weight"]:
                    raise RestoreError("Recovery read-back did not match the recorded previous setting")
                self.policy_store.set_recovery_state(point["proposal_id"], "restored")
                results.append(RestoreOutcome(record.name, "restored", point["previous_configured_weight"], "Durable recovery state verified"))
            except (RestoreError, OSError, ValueError) as error:
                try:
                    self.policy_store.set_recovery_state(point["proposal_id"], "restore_failed")
                except PersistenceError as state_error:
                    results.append(RestoreOutcome(record.name, "failed", point["previous_configured_weight"], f"{error}; recovery state update failed: {state_error}"))
                    continue
                results.append(RestoreOutcome(record.name, "failed", point["previous_configured_weight"], str(error)))
        return results
