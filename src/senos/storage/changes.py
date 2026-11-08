"""Persistent CPU restore points; originals survive process restarts."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from senos.recovery.errors import RestoreError
from senos.workloads.errors import RegistrationError
from .registry import RegistryStore
from senos.executor.locking import serialized


class CPUJournal:
    def __init__(self, database: Path):
        self.database = database
        self.store = RegistryStore(database)

    def pending(self) -> list[dict]:
        if not self.database.exists():
            return []
        try:
            with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cpu_restore_points'").fetchone():
                    return []
                return [dict(row) for row in connection.execute("SELECT * FROM cpu_restore_points WHERE restored_at IS NULL ORDER BY captured_at, workload_id")]
        except (OSError, sqlite3.Error) as error:
            raise RestoreError("Cannot read CPU restore journal") from error

    @serialized
    def capture(self, workload, state) -> None:
        try:
            with closing(self.store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                saved = connection.execute("SELECT * FROM registered_workloads WHERE id=?", (str(workload.workload_id),)).fetchone()
                if not saved or saved["unit"] != workload.unit or saved["manager"] != workload.manager:
                    raise RestoreError("Workload registration changed before capture")
                old = connection.execute("SELECT * FROM cpu_restore_points WHERE workload_id=? AND restored_at IS NULL", (str(workload.workload_id),)).fetchone()
                if old:
                    if old["invocation_id"] != state.invocation_id or old["cgroup"] != state.cgroup:
                        raise RestoreError("An earlier invocation has pending recovery; original will not be overwritten")
                    return
                connection.execute(
                    """INSERT INTO cpu_restore_points VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                       ON CONFLICT(workload_id) DO UPDATE SET unit=excluded.unit, manager=excluded.manager,
                       original_weight=excluded.original_weight, invocation_id=excluded.invocation_id,
                       cgroup=excluded.cgroup, captured_at=excluded.captured_at, restored_at=NULL, last_error=NULL""",
                    (str(workload.workload_id), workload.unit, workload.manager, state.weight, state.invocation_id, state.cgroup, datetime.now(timezone.utc).isoformat()),
                )
        except (OSError, sqlite3.Error, RegistrationError) as error:
            raise RestoreError("Cannot persist original CPU settings; do not apply a policy") from error

    @serialized
    def finish(self, identifier: str, captured_at: str, operation) -> str | None:
        """Serialize recovery with unregister; retain pending rows on failure."""
        try:
            with closing(self.store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                point = connection.execute("SELECT * FROM cpu_restore_points WHERE workload_id=? AND restored_at IS NULL", (identifier,)).fetchone()
                if point is None:
                    return None
                if point["captured_at"] != captured_at:
                    return "Restore point changed concurrently; retry recovery"
                saved = connection.execute("SELECT * FROM registered_workloads WHERE id=?", (identifier,)).fetchone()
                if not saved or saved["unit"] != point["unit"] or saved["manager"] != point["manager"]:
                    raise RestoreError("Recorded workload is no longer registered with the same target")
                try:
                    operation(dict(point))
                except RestoreError as error:
                    connection.execute("UPDATE cpu_restore_points SET last_error=? WHERE workload_id=?", (str(error), identifier))
                    return str(error)
                connection.execute("UPDATE cpu_restore_points SET restored_at=?, last_error=NULL WHERE workload_id=?", (datetime.now(timezone.utc).isoformat(), identifier))
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cpu_execution_sessions'").fetchone():
                    connection.execute("UPDATE cpu_execution_sessions SET status='restored' WHERE workload_id=? AND status='active'", (identifier,))
                return None
        except (OSError, sqlite3.Error, RegistrationError) as error:
            raise RestoreError("Cannot update CPU restore journal; recovery remains pending") from error
