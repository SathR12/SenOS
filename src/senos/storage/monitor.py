"""Durable observe-only session state and decision history, separate from registry."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from senos.contracts import Intent


class MonitorError(Exception):
    """Readable monitoring configuration, collection, or persistence error."""


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class MonitorStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self, *, write=False):
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=5)
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            if write:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executescript("""CREATE TABLE IF NOT EXISTS monitor_runs (
                    run_id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, registry_path TEXT NOT NULL,
                    deadline TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','expired','stopped','interrupted','failed','limited')),
                    reason TEXT NOT NULL, cycles INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS monitor_events (
                    run_id TEXT NOT NULL REFERENCES monitor_runs(run_id),
                    sequence INTEGER NOT NULL, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );""")
            return connection
        except (OSError, sqlite3.Error) as error:
            if "connection" in locals():
                connection.close()
            raise MonitorError("Cannot open monitor database; check its path and permissions") from error

    def create(self, intent: Intent, registry: Path, deadline: datetime) -> str:
        run_id = str(uuid4())
        try:
            with closing(self._connect(write=True)) as connection, connection:
                now = timestamp()
                connection.execute("INSERT INTO monitor_runs VALUES (?, ?, ?, ?, ?, ?, 'running', '', 0)",
                    (run_id, intent.model_dump_json(), str(registry.resolve()), deadline.isoformat(), now, now))
        except sqlite3.Error as error:
            raise MonitorError("Cannot save monitoring session") from error
        return run_id

    def get(self, run_id: str) -> dict:
        try:
            run_id = str(UUID(run_id))
        except (ValueError, TypeError):
            raise MonitorError("Monitor run ID must be a valid UUID") from None
        if not self.path.exists():
            raise MonitorError("Monitoring session was not found")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT * FROM monitor_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise MonitorError("Monitoring session was not found")
            result = dict(row)
            result["intent"] = json.loads(result.pop("intent_json"))
            return result
        except (sqlite3.Error, ValueError) as error:
            raise MonitorError("Cannot read monitoring session; check saved data") from error

    def append(self, run_id: str, payload: dict) -> bool:
        try:
            with closing(self._connect(write=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT cycles,status FROM monitor_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None or row["status"] != "running":
                    return False
                now = timestamp()
                sequence = row["cycles"] + 1
                connection.execute("INSERT INTO monitor_events VALUES (?, ?, ?, ?)", (run_id, sequence, now, json.dumps(payload)))
                connection.execute("UPDATE monitor_runs SET cycles=?,updated_at=? WHERE run_id=?", (sequence, now, run_id))
            return True
        except (sqlite3.Error, ValueError) as error:
            raise MonitorError("Cannot save monitor event; monitoring stopped") from error

    def finish(self, run_id: str, status: str, reason: str, *, force=False) -> None:
        if status not in {"expired", "stopped", "interrupted", "failed", "limited"}:
            raise MonitorError("Invalid monitoring completion status")
        try:
            with closing(self._connect(write=True)) as connection, connection:
                connection.execute("UPDATE monitor_runs SET status=?,reason=?,updated_at=? WHERE run_id=? AND (status='running' OR ?)",
                    (status, reason, timestamp(), run_id, force))
        except sqlite3.Error as error:
            raise MonitorError("Cannot finalize monitoring session") from error

    def stop(self, run_id: str) -> dict:
        run_id = self.get(run_id)["run_id"]
        self.finish(run_id, "stopped", "Stop requested by user")
        return self.get(run_id)

    def list_runs(self, limit=20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise MonitorError("Choose a session limit from 1 through 100")
        if not self.path.exists():
            return []
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT run_id FROM monitor_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [self.get(row["run_id"]) for row in rows]
        except sqlite3.Error as error:
            raise MonitorError("Cannot list monitoring sessions") from error

    def events(self, run_id: str, limit=20) -> list[dict]:
        run_id = self.get(run_id)["run_id"]
        if not 1 <= limit <= 100:
            raise MonitorError("Choose an event limit from 1 through 100")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT sequence,observed_at,payload_json FROM monitor_events WHERE run_id=? ORDER BY sequence DESC LIMIT ?", (run_id, limit)).fetchall()
            return [{"sequence": row["sequence"], "observed_at": row["observed_at"], "evaluation": json.loads(row["payload_json"])} for row in reversed(rows)]
        except (sqlite3.Error, ValueError) as error:
            raise MonitorError("Cannot read monitor events") from error
