"""Load only supported agent settings from a local .env, without shell evaluation."""

import os
from pathlib import Path
import shlex

from .provider import AgentError

ALLOWED = {"GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "SENOS_LLM_PROVIDER", "SENOS_GEMINI_MODEL", "SENOS_OPENAI_MODEL"}


def load_agent_env(path: Path | None = None) -> None:
    path = path if path is not None else Path.cwd() / ".env"
    if not path.exists():
        return
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise AgentError("Cannot read the local .env file") from None
    settings = {}
    for number, line in enumerate(source.splitlines(), 1):
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw = line.partition("=")
        name = name.strip()
        if name not in ALLOWED or not separator:
            continue
        try:
            values = shlex.split(raw, comments=True, posix=True)
            if len(values) > 1:
                raise ValueError()
        except ValueError:
            raise AgentError(f"Invalid quoting in .env setting on line {number}") from None
        settings[name] = values[0] if values else ""
    for name, value in settings.items():
        os.environ.setdefault(name, value)
