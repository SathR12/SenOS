"""Application service for deterministic, registry-backed user objectives."""

from pydantic import ValidationError

from senos.contracts import Intent, IntentKind
from senos.workloads.errors import RegistrationError
from senos.workloads.registry import Registry

from .errors import IntentError


class IntentService:
    """Resolve CLI workload names and produce shared Intent contracts."""

    def __init__(self, registry: Registry):
        self.registry = registry

    def prioritize(
        self,
        target_name: str,
        duration_seconds: int,
        preserve_names: tuple[str, ...] = (),
    ) -> Intent:
        """Create a bounded prioritize intent without executing a policy."""
        try:
            target = self.registry.get_by_name(target_name)
            preserved = tuple(self.registry.get_by_name(name) for name in preserve_names)
            source_text = self._source_text(
                target.name,
                duration_seconds,
                tuple(workload.name for workload in preserved),
            )
            return Intent(
                kind=IntentKind.PRIORITIZE,
                target_workload_id=target.workload_id,
                preserve_workload_ids=tuple(workload.workload_id for workload in preserved),
                duration_seconds=duration_seconds,
                source_text=source_text,
            )
        except (RegistrationError, ValidationError) as error:
            raise IntentError(self._message(error)) from error

    @staticmethod
    def _source_text(
        target_name: str,
        duration_seconds: int,
        preserve_names: tuple[str, ...],
    ) -> str:
        text = f"prioritize {target_name} for {duration_seconds} seconds"
        if preserve_names:
            text += f"; preserve {', '.join(preserve_names)}"
        return text

    @staticmethod
    def _message(error: RegistrationError | ValidationError) -> str:
        if isinstance(error, RegistrationError):
            return str(error)
        first = error.errors(include_url=False)[0]
        return str(first["msg"]).removeprefix("Value error, ")
