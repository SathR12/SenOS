"""Persistent execution ownership and action audit records."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from senos.recovery.errors import RestoreError


class ExecutionStore:
    def __init__(self, database: Path):
        self.database = database

    def connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.executescript("""CREATE TABLE IF NOT EXISTS cpu_execution_sessions (
            session_id TEXT PRIMARY KEY, workload_id TEXT NOT NULL, intent_json TEXT NOT NULL,
            workload_json TEXT NOT NULL, invocation_id TEXT NOT NULL, cgroup TEXT NOT NULL,
            original_effective INTEGER NOT NULL, deadline TEXT NOT NULL, status TEXT NOT NULL,
            owner_pid INTEGER NOT NULL, owner_start TEXT NOT NULL, recovery_ready INTEGER NOT NULL DEFAULT 0,
            last_change_at TEXT, last_applied INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS cpu_execution_owner ON cpu_execution_sessions(workload_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS cpu_execution_actions (
            result_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, result_json TEXT NOT NULL
        );""")
        return connection

    def create(self, values):
        try:
            with closing(self.connect()) as connection, connection:
                connection.execute("INSERT INTO cpu_execution_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL)", values)
        except sqlite3.Error as error:
            raise RestoreError("Cannot acquire execution ownership; another objective may own this workload") from error

    def get(self, session_id):
        try:
            with closing(self.connect()) as connection:
                row = connection.execute("SELECT * FROM cpu_execution_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise RestoreError("CPU execution session not found")
            return dict(row)
        except sqlite3.Error as error:
            raise RestoreError("Cannot read CPU execution ownership") from error

    def ready(self, session_id):
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE cpu_execution_sessions SET recovery_ready=1 WHERE session_id=? AND status='active'", (session_id,))

    def record(self, session_id, result):
        try:
            with closing(self.connect()) as connection, connection:
                connection.execute("INSERT INTO cpu_execution_actions VALUES (?,?,?)", (str(result.result_id), session_id, result.model_dump_json()))
                if result.status.value == "applied":
                    connection.execute("UPDATE cpu_execution_sessions SET last_change_at=?,last_applied=? WHERE session_id=?", (result.finished_at.isoformat(), result.effective_cpu_weight, session_id))
        except sqlite3.Error as error:
            raise RestoreError("Cannot persist CPU action result; restore original settings") from error

    def end_without_change(self, session_id):
        try:
            with closing(self.connect()) as connection, connection:
                connection.execute("UPDATE cpu_execution_sessions SET status='restored' WHERE session_id=? AND last_applied IS NULL", (session_id,))
        except sqlite3.Error as error:
            raise RestoreError("Cannot close execution ownership") from error

    def plan_write(self, session_id, weight, when):
        try:
            with closing(self.connect()) as connection, connection:
                connection.execute("UPDATE cpu_execution_sessions SET last_applied=?,last_change_at=? WHERE session_id=? AND status='active'", (weight, when.isoformat(), session_id))
        except sqlite3.Error as error:
            raise RestoreError("Cannot persist CPU write plan; no CPU write is authorized") from error
