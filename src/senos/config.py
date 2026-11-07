"""Local runtime paths; registration does not need elevated privileges."""

import os
from pathlib import Path


def default_registry_path() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".local" / "state"
    return base / "senos" / "registry.sqlite3"
