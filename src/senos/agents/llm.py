"""Bounded single-agent observe/reason/evaluate loop. No resource execution."""

import json
from pathlib import Path

from pydantic import ValidationError

from senos.contracts import Intent
from senos.intent import IntentError, IntentService
from senos.intent.pipeline import evaluate_intent
from senos.telemetry.status import StatusError, get_status
from senos.workloads.errors import RegistrationError
from senos.workloads.registry import Registry

from .llm_tools import TOOL_MODELS, tool_definitions
from .provider import AgentError, ModelProvider, create_provider

MAX_STEPS = 6
MAX_WORKLOADS = 64
PROMPT = Path(__file__).with_name("prompts") / "resource_advisor.md"


def run_agent(request: str, database: Path, *, provider: ModelProvider | None = None, model: str | None = None, provider_name: str | None = None) -> dict:
    request = request.strip()
    if not request or len(request) > 1000:
        raise AgentError("Describe your objective in 1 to 1000 characters")
    registry = Registry(database)
    try:
        inventory = registry.list_workloads()
    except (RegistrationError, ValueError) as error:
        raise AgentError("Cannot read workload registry; check its path and saved data") from error
    if not inventory:
        raise AgentError("Register Linux workloads first; the agent cannot manage arbitrary applications")
    if len(inventory) > MAX_WORKLOADS:
        raise AgentError("The initial agent supports at most 64 registered workloads per run")
    provider = provider if provider is not None else create_provider(model=model, name=provider_name)
    history = [{"role": "user", "content": request}]
    trace = []
    listed = inspected = False
    seen_calls = set()

    def unchanged():
        try:
            if registry.list_workloads() != inventory:
                raise AgentError("Registered inventory changed during the agent run; submit the request again")
        except (RegistrationError, ValueError) as error:
            raise AgentError("Cannot recheck registered inventory") from error

    for step in range(1, MAX_STEPS + 1):
        unchanged()
        response = provider.respond(history, tool_definitions(), PROMPT.read_text(encoding="utf-8"))
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise AgentError("Model response did not complete")
        output = response.get("output")
        if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
            raise AgentError("Model returned an invalid tool response")
        calls = [item for item in output if item.get("type") == "function_call"]
        if len(calls) != 1:
            raise AgentError("Expected exactly one allowlisted tool call; no policy was applied")
        call = calls[0]
        name, call_id = call.get("name"), call.get("call_id")
        if not isinstance(name, str) or name not in TOOL_MODELS or not isinstance(call_id, str) or not call_id or call_id in seen_calls:
            raise AgentError("Model requested an unknown tool or invalid call identity")
        seen_calls.add(call_id)
        arguments = call.get("arguments")
        if not isinstance(arguments, str) or len(arguments) > 8192:
            raise AgentError("Model supplied invalid tool arguments")
        try:
            args = TOOL_MODELS[name][0].model_validate_json(arguments)
        except (ValidationError, ValueError):
            raise AgentError(f"Model supplied invalid arguments for {name}") from None
        unchanged()
        terminal = None
        try:
            if name == "list_workloads":
                payload = {"workloads": [workload.model_dump(mode="json") for workload in inventory]}
                listed = True
            elif name == "inspect_resources":
                if not listed:
                    raise AgentError("Agent must list workloads before reading resources")
                snapshot = get_status(database)
                payload = {"live_telemetry": snapshot.live, "workloads": [
                    {"workload": row.workload.model_dump(mode="json"), "observation": row.observation.model_dump(mode="json")}
                    for row in snapshot.rows]}
                inspected = True
            elif name == "evaluate_cpu_intent":
                if not listed or not inspected:
                    raise AgentError("Agent must discover workloads and inspect resources before evaluation")
                intent = IntentService(registry).prioritize(args.target_name, args.duration_seconds, tuple(args.preserve_names))
                # Retain the actual user request without regenerating IDs or timestamps.
                intent = Intent.model_validate({**intent.model_dump(), "source_text": request})
                payload = evaluate_intent(intent, database)
                terminal = {"status": "evaluated", "evaluation": payload, "rationale": args.rationale}
            elif name == "ask_clarification":
                payload = {"question": args.question}
                terminal = {"status": "clarification", **payload}
            else:
                payload = {"reason": args.reason}
                terminal = {"status": "unsupported", **payload}
        except (IntentError, StatusError, RegistrationError, ValueError) as error:
            raise AgentError(f"Agent tool {name} failed: {error}") from error
        unchanged()
        trace.append({"step": step, "tool": name, "arguments": args.model_dump(mode="json")})
        if terminal is not None:
            return {"mode": "dry_run", "executed": False, **terminal,
                "agent": {"provider": provider.name, "model": provider.model, "steps": step, "tool_trace": trace}}
        history.extend(output)
        history.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps(payload)})
    raise AgentError("Agent reached its six-call limit without a validated result; no policy was applied")
