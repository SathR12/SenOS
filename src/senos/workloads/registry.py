"""Registration application service, independent of CLI presentation."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from senos.contracts import Workload
from senos.contracts.workload import validate_workload_name
from senos.storage.registry import RegistryStore
from .errors import RegistrationError
from .systemd import SystemdResolver, UnitInfo, validate_unit


class UnitResolver(Protocol):
    def resolve(self, unit: str, manager: str) -> UnitInfo: ...


def _validated_name(name: str) -> str:
    try:
        return validate_workload_name(name)
    except ValueError as error:
        raise RegistrationError(str(error)) from error


class Registry:
    def __init__(self, database: Path, resolver: UnitResolver | None = None):
        self.store = RegistryStore(database)
        self.resolver = resolver if resolver is not None else SystemdResolver()

    def register(self, name: str, unit: str, manager: str = "user") -> tuple[Workload, bool]:
        name = _validated_name(name)
        validate_unit(unit)
        if manager not in {"user", "system"}:
            raise RegistrationError("Manager must be user or system")
        resolved = self.resolver.resolve(unit, manager)
        # Unit state is checked before persistence, including repeat registrations.
        # Later observation/execution must resolve it again; cgroups can change.
        record, created = self.store.add(name, resolved.unit, manager)
        return record.to_workload(), created

    def list_workloads(self) -> list[Workload]:
        return [record.to_workload() for record in self.store.list_workloads()]

    def get_by_name(self, name: str) -> Workload:
        """Resolve an exact, case-sensitive display name to a shared contract."""
        return self.store.get_by_name(_validated_name(name)).to_workload()

    def get_by_id(self, workload_id: UUID | str) -> Workload:
        """Resolve a stable workload UUID to a shared contract."""
        try:
            identifier = str(UUID(str(workload_id)))
        except ValueError as error:
            raise RegistrationError("Workload ID must be a valid UUID") from error
        return self.store.get_by_id(identifier).to_workload()

    def unregister(self, name: str | None = None, *, all_workloads: bool = False) -> int:
        """Remove explicit registrations without contacting or stopping units."""
        if (name is not None and all_workloads) or (name is None and not all_workloads):
            raise RegistrationError("Choose a workload name or all workloads, not both")
        if name is not None:
            name = _validated_name(name)
        return self.store.remove(name)
