"""SQLite persistence for explicit workload registrations.

RegisteredWorkload is the persistence record; to_workload maps it explicitly
to the shared Workload contract without generating a new identity.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from senos.contracts import Workload
from senos.workloads.errors import RegistrationError


@dataclass(frozen=True)
class RegisteredWorkload:
    id: str
    name: str
    unit: str
    manager: str
    registered_at: str

    def to_workload(self) -> Workload:
        """Preserve persisted identity, timestamp, and manager for consumers."""
        return Workload(
            workload_id=self.id,
            name=self.name,
            unit=self.unit,
            manager=self.manager,
            registered_at=self.registered_at,
        )


class RegistryStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS registered_workloads (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                unit TEXT NOT NULL,
                manager TEXT NOT NULL CHECK(manager IN ('user', 'system')),
                registered_at TEXT NOT NULL,
                UNIQUE(unit, manager)
            )""")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def add(self, name: str, unit: str, manager: str) -> tuple[RegisteredWorkload, bool]:
        try:
            with closing(self._connect()) as connection, connection:
                identifier = str(uuid4())
                timestamp = datetime.now(timezone.utc).isoformat()
                cursor = connection.execute(
                    "INSERT INTO registered_workloads (id, name, unit, manager, registered_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (identifier, name, unit, manager, timestamp),
                )
                row = connection.execute("SELECT * FROM registered_workloads WHERE name = ?", (name,)).fetchone()
                if row is None or row["unit"] != unit or row["manager"] != manager:
                    raise RegistrationError("Name or unit is already registered to a different workload")
                return RegisteredWorkload(**dict(row)), cursor.rowcount == 1
        except (OSError, sqlite3.Error) as error:
            raise RegistrationError("Cannot access registry database; check its path and permissions") from error

    def list_workloads(self) -> list[RegisteredWorkload]:
        if not self.path.exists():
            return []
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT * FROM registered_workloads ORDER BY name").fetchall()
                return [RegisteredWorkload(**dict(row)) for row in rows]
        except (OSError, sqlite3.Error) as error:
            raise RegistrationError("Cannot read registry database; check its path and permissions") from error


    def remove(self, name: str | None = None) -> int:
        """Delete one named record, or all records when name is None.

        Only registry rows are removed. Resource-policy history must be checked
        before deletion once policy execution is implemented.
        """
        if not self.path.exists():
            if name is None:
                return 0
            raise RegistrationError(f"Workload {name!r} is not registered")
        try:
            with closing(self._connect()) as connection, connection:
                if name is None:
                    cursor = connection.execute("DELETE FROM registered_workloads")
                else:
                    cursor = connection.execute("DELETE FROM registered_workloads WHERE name = ?", (name,))
                    if cursor.rowcount == 0:
                        raise RegistrationError(f"Workload {name!r} is not registered")
                return cursor.rowcount
        except (OSError, sqlite3.Error) as error:
            raise RegistrationError("Cannot update registry database; check its path and permissions") from error
