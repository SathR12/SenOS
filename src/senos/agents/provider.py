"""Minimal OpenAI Responses adapter; no SDK dependency or resource-control access."""

import json
import os
import socket
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AgentError(Exception):
    """An agent run could not safely produce a result."""


class ModelProvider(Protocol):
    name: str
    model: str
    def respond(self, history: list[dict], tools: list[dict], instructions: str) -> dict: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_request(request):
    # Keep Authorization on the fixed API endpoint; never follow a redirect.
    return build_opener(NoRedirect()).open(request, timeout=60)


class OpenAIProvider:
    name = "openai"

    def __init__(self, *, model: str | None = None):
        self.model = (model or os.environ.get("SENOS_OPENAI_MODEL", "gpt-4.1-mini")).strip()
        self._key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self._key:
            raise AgentError("Set OPENAI_API_KEY in your environment before running the LLM agent")
        if not self.model or len(self.model) > 128:
            raise AgentError("Set a valid model with --model or SENOS_OPENAI_MODEL")

    def respond(self, history: list[dict], tools: list[dict], instructions: str) -> dict:
        payload = {"model": self.model, "instructions": instructions, "input": history,
            "tools": tools, "tool_choice": "required", "parallel_tool_calls": False,
            "max_output_tokens": 2048, "store": False, "include": ["reasoning.encrypted_content"]}
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        try:
            with send_request(request) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise AgentError("Model response exceeded the supported size")
            result = json.loads(raw)
        except HTTPError as error:
            # Never surface provider bodies: they can echo requests or credentials.
            error.close()
            messages = {401: "OpenAI authentication failed; check OPENAI_API_KEY",
                403: "OpenAI refused access; check project permissions and model access",
                429: "OpenAI rate or quota limit reached; check billing and retry later"}
            raise AgentError(messages.get(error.code, f"OpenAI request failed (HTTP {error.code}); check model access and retry")) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise AgentError("Cannot reach OpenAI within the timeout; check connectivity and retry") from None
        except (ValueError, UnicodeError):
            raise AgentError("OpenAI returned invalid JSON") from None
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise AgentError("Model response was incomplete or refused; no policy was applied")
        return result



def create_provider(*, model: str | None = None, name: str | None = None) -> ModelProvider:
    from .environment import load_agent_env
    from .gemini import GeminiProvider

    load_agent_env()
    selected = (name or os.environ.get("SENOS_LLM_PROVIDER") or
        ("gemini" if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") else "openai")).strip().lower()
    if selected == "gemini":
        return GeminiProvider(model=model)
    if selected == "openai":
        return OpenAIProvider(model=model)
    raise AgentError("Choose gemini or openai with --provider or SENOS_LLM_PROVIDER")
