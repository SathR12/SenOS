"""Durable records for validated policy inputs and outcomes."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from pydantic import ValidationError

from senos.contracts import (
    ActionResult,
    ExecutionSnapshot,
    Intent,
    Observation,
    Proposal,
)


class PersistenceError(ValueError):
    """A stored record is unavailable, invalid, or cannot be written."""


class PolicyStore:
    """SQLite store with append-only policy records.

    Workload registrations are deliberately not foreign keys: unregistering a
    workload must not erase the audit history that refers to its UUID.
    """

    _MIGRATIONS = (
        """
        CREATE TABLE IF NOT EXISTS intents (
            intent_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('prioritize', 'restore')),
            target_workload_id TEXT NOT NULL,
            preserve_workload_ids TEXT NOT NULL,
            duration_seconds INTEGER,
            source_text TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state = 'validated'),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS execution_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('collected', 'aborted')),
            failure_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS observations (
            snapshot_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            workload_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, workload_id, observed_at),
            FOREIGN KEY(snapshot_id) REFERENCES execution_snapshots(snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            workload_id TEXT,
            status TEXT NOT NULL CHECK(status IN ('proposed', 'no_action')),
            expected_cpu_weight INTEGER,
            proposed_cpu_weight INTEGER,
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            FOREIGN KEY(intent_id) REFERENCES intents(intent_id)
        );
        CREATE TABLE IF NOT EXISTS validation_decisions (
            decision_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL UNIQUE,
            decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
            reason TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id)
        );
        CREATE TABLE IF NOT EXISTS recovery_records (
            proposal_id TEXT PRIMARY KEY,
            workload_id TEXT NOT NULL,
            previous_configured_weight INTEGER,
            current_effective_weight INTEGER,
            invocation_id TEXT NOT NULL,
            cgroup_identity TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id)
        );
        CREATE TABLE IF NOT EXISTS action_results (
            result_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            workload_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('applied', 'rejected', 'failed')),
            requested_cpu_weight INTEGER NOT NULL,
            previous_cpu_weight INTEGER,
            effective_cpu_weight INTEGER,
            reason TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id)
        );
        """,
    )

    def __init__(self, database: Path):
        self.database = database

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, migration in enumerate(self._MIGRATIONS, 1):
                if version in applied:
                    continue
                connection.executescript(migration)
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
            connection.commit()
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    def save_intent(self, intent: Intent) -> Intent:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO intents
                    (intent_id, kind, target_workload_id, preserve_workload_ids,
                     duration_seconds, source_text, submitted_at, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'validated', ?)""",
                    (
                        str(intent.intent_id), intent.kind.value,
                        str(intent.target_workload_id),
                        self._json([str(identifier) for identifier in intent.preserve_workload_ids]),
                        intent.duration_seconds, intent.source_text,
                        intent.submitted_at.isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Intent already exists or cannot be stored") from error
        return intent

    def get_intent(self, intent_id: UUID | str) -> Intent:
        try:
            identifier = str(UUID(str(intent_id)))
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM intents WHERE intent_id = ?", (identifier,)
                ).fetchone()
        except (ValueError, sqlite3.Error) as error:
            raise PersistenceError("Cannot read persisted intent") from error
        if row is None:
            raise PersistenceError("Persisted intent was not found")
        try:
            return Intent(
                intent_id=row["intent_id"], kind=row["kind"],
                target_workload_id=row["target_workload_id"],
                preserve_workload_ids=tuple(json.loads(row["preserve_workload_ids"])),
                duration_seconds=row["duration_seconds"],
                source_text=row["source_text"], submitted_at=row["submitted_at"],
            )
        except (ValidationError, TypeError, json.JSONDecodeError) as error:
            raise PersistenceError("Persisted intent is invalid") from error

    def save_snapshot(self, snapshot: ExecutionSnapshot) -> ExecutionSnapshot:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO execution_snapshots VALUES (?, ?, ?, 'collected', NULL)",
                    (str(snapshot.snapshot_id), str(snapshot.intent_id), snapshot.collected_at.isoformat()),
                )
                for observation in snapshot.observations:
                    connection.execute(
                        "INSERT INTO observations VALUES (?, ?, ?, ?, ?)",
                        (str(snapshot.snapshot_id), str(snapshot.intent_id),
                         str(observation.workload_id), observation.observed_at.isoformat(),
                         observation.model_dump_json()),
                    )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Snapshot already exists or cannot be stored") from error
        return snapshot

    def save_proposal(self, proposal: Proposal) -> Proposal:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO proposals VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?)""",
                    (str(proposal.proposal_id), str(proposal.intent_id),
                     str(proposal.workload_id), proposal.expected_cpu_weight,
                     proposal.proposed_cpu_weight, proposal.rationale,
                     proposal.created_at.isoformat(), proposal.expires_at.isoformat()),
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Proposal already exists or cannot be stored") from error
        return proposal

    def save_no_action(self, intent: Intent, reason: str, *, now: datetime) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO proposals
                    (proposal_id, intent_id, workload_id, status, rationale, created_at)
                    VALUES (?, ?, NULL, 'no_action', ?, ?)""",
                    (str(uuid4()), str(intent.intent_id), reason, now.isoformat()),
                )
        except sqlite3.Error as error:
            raise PersistenceError("No-action decision cannot be stored") from error

    def save_action_result(self, result: ActionResult) -> ActionResult:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO action_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(result.result_id), str(result.proposal_id), str(result.workload_id),
                     result.status.value, result.requested_cpu_weight,
                     result.previous_cpu_weight, result.effective_cpu_weight,
                     result.reason, result.started_at.isoformat(), result.finished_at.isoformat()),
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Action result already exists or cannot be stored") from error
        return result

    def save_validation(self, decision: object, *, decided_at: datetime | None = None) -> None:
        """Persist one immutable validator decision for a proposal."""
        decided_at = decided_at or datetime.now(timezone.utc)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO validation_decisions
                    (decision_id, proposal_id, decision, reason, decided_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        str(uuid4()), str(decision.proposal_id),
                        "accepted" if decision.accepted else "rejected",
                        decision.reason, decided_at.isoformat(),
                    ),
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Validation decision already exists or cannot be stored") from error

    def save_recovery(
        self,
        *,
        proposal_id: UUID,
        workload_id: UUID,
        previous_configured_weight: int | None,
        current_effective_weight: int | None,
        invocation_id: str,
        cgroup_identity: str,
        expires_at: datetime,
        state: str = "pending_apply",
    ) -> None:
        if not invocation_id or not cgroup_identity:
            raise PersistenceError("Unit identity is required for recovery")
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """INSERT INTO recovery_records VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(proposal_id), str(workload_id),
                        previous_configured_weight, current_effective_weight,
                        invocation_id, cgroup_identity, expires_at.isoformat(),
                        state, datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as error:
            raise PersistenceError("Recovery record already exists or cannot be stored") from error
