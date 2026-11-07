"""Registration application service, independent of CLI presentation."""

from pathlib import Path
from typing import Protocol

from senos.storage.registry import RegisteredWorkload, RegistryStore
from .errors import RegistrationError
from .systemd import SystemdResolver, UnitInfo, validate_unit


class UnitResolver(Protocol):
    def resolve(self, unit: str, manager: str) -> UnitInfo: ...


def _validated_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 128 or any(not character.isprintable() for character in name):
        raise RegistrationError("Workload name must contain 1 to 128 printable characters")
    return name


class Registry:
    def __init__(self, database: Path, resolver: UnitResolver | None = None):
        self.store = RegistryStore(database)
        self.resolver = resolver if resolver is not None else SystemdResolver()

    def register(self, name: str, unit: str, manager: str = "user") -> tuple[RegisteredWorkload, bool]:
        name = _validated_name(name)
        validate_unit(unit)
        if manager not in {"user", "system"}:
            raise RegistrationError("Manager must be user or system")
        resolved = self.resolver.resolve(unit, manager)
        # Unit state is checked before persistence, including repeat registrations.
        # Later observation/execution must resolve it again; cgroups can change.
        return self.store.add(name, resolved.unit, manager)

    def list_workloads(self) -> list[RegisteredWorkload]:
        return self.store.list_workloads()


    def unregister(self, name: str | None = None, *, all_workloads: bool = False) -> int:
        """Remove explicit registrations without contacting or stopping units."""
        if (name is not None and all_workloads) or (name is None and not all_workloads):
            raise RegistrationError("Choose a workload name or all workloads, not both")
        if name is not None:
            name = _validated_name(name)
        return self.store.remove(name)
