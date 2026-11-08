"""Read-only intent-to-proposal path, reusable by future intent providers."""

from datetime import datetime, timezone
from pathlib import Path

from senos.agents.cpu import CPUAgent
from senos.agents.live import collect_live_context
from senos.contracts import Intent

from .errors import IntentError


def evaluate_intent(intent: Intent, database: Path) -> dict:
    """Collect evidence and evaluate the exact supplied intent without executing it."""
    try:
        context, warnings = collect_live_context(database, intent=intent)
        decision = CPUAgent().evaluate(context, now=datetime.now(timezone.utc))
    except (OSError, ValueError) as error:
        raise IntentError(str(error)) from error
    return {
        "accepted": True, "executed": False, "mode": "dry_run", "source": "live",
        "intent": intent.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "warnings": list(warnings), "decision": decision.model_dump(mode="json"),
    }
