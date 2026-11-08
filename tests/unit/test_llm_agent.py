from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from senos.agents import llm, provider
from senos.agents.live import collect_live_context
from senos.agents.llm_tools import tool_definitions
from senos.cli import app
from senos.contracts import Observation
from senos.intent import pipeline
from senos.telemetry.linux import CPUWeightReading
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo


def call(name, arguments=None, identifier=None):
    return {"status": "completed", "output": [{"type": "function_call", "name": name,
        "arguments": json.dumps(arguments or {}), "call_id": identifier or name}]}


class ScriptedProvider:
    name = "scripted-test-provider"
    model = "offline-fixture"
    def __init__(self, responses):
        self.responses = iter(responses)
        self.histories = []
    def respond(self, history, tools, instructions):
        self.histories.append(list(history))
        assert "untrusted data" in instructions
        return next(self.responses)


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, "/user.slice/app.slice/" + unit)


class Collector:
    def __init__(self):
        self.now = datetime.now(timezone.utc)
        self.pressure = 20
    def read_cpu_weight(self, workload):
        return CPUWeightReading(100, "a" * 32, Path("/sys/fs/cgroup/app.slice") / workload.unit)
    def observe_all(self, workloads):
        self.now += timedelta(seconds=0.5)
        return [Observation(workload_id=w.workload_id, observed_at=self.now, state="active", cpu_usage_percent=50, cpu_pressure_some_avg10=self.pressure) for w in workloads]
    def pause(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def registered(tmp_path, monkeypatch):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    registry.register("web", "web.service")
    registry.register("build", "build.service")
    collector = Collector()
    def inspect(database):
        workloads = registry.list_workloads()
        observations = collector.observe_all(workloads)
        return SimpleNamespace(live=True, rows=[SimpleNamespace(workload=w, observation=o) for w, o in zip(workloads, observations)])
    monkeypatch.setattr(llm, "get_status", inspect)
    monkeypatch.setattr(pipeline, "collect_live_context", lambda database, *, intent: collect_live_context(database, intent=intent, telemetry=collector, pause=collector.pause))
    monkeypatch.setattr(pipeline, "datetime", SimpleNamespace(now=lambda tz: collector.now))
    return database, registry, collector


def sequence(**overrides):
    arguments = {"target_name": "web", "preserve_names": ["build"], "duration_seconds": 600,
        "rationale": "Prioritize the requested web workload; observed CPU pressure supports evaluating a bounded proposal."}
    arguments.update(overrides)
    return [call("list_workloads"), call("inspect_resources"), call("evaluate_cpu_intent", arguments)]


@pytest.mark.parametrize("pressure,status", [(20, "proposed"), (0, "no_action")])
def test_single_agent_uses_tools_and_real_deterministic_pipeline(registered, monkeypatch, pressure, status):
    database, registry, collector = registered
    collector.pressure = pressure
    before = database.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("LLM agent must never write resource policies or restoration data")
    monkeypatch.setattr("senos.executor.cpu.SystemdCPUBackend.write", forbidden)
    monkeypatch.setattr("senos.storage.changes.CPUJournal.capture", forbidden)
    fake = ScriptedProvider(sequence())
    request = "Prioritize web for ten minutes while keeping build progressing"
    result = llm.run_agent(request, database, provider=fake)
    assert result["status"] == "evaluated" and result["executed"] is False
    assert result["agent"]["steps"] == 3
    evaluation = result["evaluation"]
    assert evaluation["intent"]["source_text"] == request
    assert evaluation["intent"] == evaluation["context"]["intent"]
    assert evaluation["decision"]["status"] == status
    if status == "proposed":
        assert evaluation["decision"]["proposal"]["proposed_cpu_weight"] == 200
    assert database.read_bytes() == before
    # The next model turn receives actual observations, not fabricated assistant text.
    assert json.loads(fake.histories[2][-1]["output"])["live_telemetry"] is True


@pytest.mark.parametrize("name,args,status", [
    ("ask_clarification", {"question": "Which workload and for how long?"}, "clarification"),
    ("decline_request", {"reason": "Memory tuning is not supported"}, "unsupported"),
])
def test_clarification_and_unsupported_requests_do_not_collect_or_evaluate(registered, monkeypatch, name, args, status):
    database, _, _ = registered
    def forbidden(*args):
        pytest.fail("Do not collect or evaluate an unresolved request")
    monkeypatch.setattr(llm, "get_status", forbidden)
    monkeypatch.setattr(llm, "evaluate_intent", forbidden)
    result = llm.run_agent("Help my app", database, provider=ScriptedProvider([call(name, args)]))
    assert result["status"] == status and result["executed"] is False
    assert "evaluation" not in result


@pytest.mark.parametrize("override", [
    {"target_name": "missing"}, {"preserve_names": ["missing"]}, {"preserve_names": ["web"]},
    {"preserve_names": ["build", "build"]}, {"duration_seconds": True}, {"duration_seconds": 86401},
    {"command": "rm -rf /"},
])
def test_untrusted_objectives_fail_closed(registered, override):
    database, _, _ = registered
    before = database.read_bytes()
    with pytest.raises(provider.AgentError):
        llm.run_agent("Prioritize web", database, provider=ScriptedProvider(sequence(**override)))
    assert database.read_bytes() == before


@pytest.mark.parametrize("response", [
    call("run_shell", {"command": "anything"}),
    {"status": "completed", "output": [{"type": "message", "content": "I applied the changes"}]},
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": ["invalid"]},
    {"status": "completed", "output": [{"type": "function_call", "name": [], "arguments": "{}", "call_id": "x"}]},
])
def test_invalid_model_responses_are_not_treated_as_recommendations(registered, response):
    database, _, _ = registered
    with pytest.raises(provider.AgentError):
        llm.run_agent("Prioritize web", database, provider=ScriptedProvider([response]))


def test_agent_cannot_evaluate_before_observing(registered):
    database, _, _ = registered
    with pytest.raises(provider.AgentError, match="inspect resources"):
        llm.run_agent("Prioritize web", database, provider=ScriptedProvider([sequence()[-1]]))


def test_registry_changes_during_model_call_abort_run(registered):
    database, registry, _ = registered
    fake = ScriptedProvider([call("list_workloads")])
    original = fake.respond
    def change(*args):
        registry.unregister("build")
        return original(*args)
    fake.respond = change
    with pytest.raises(provider.AgentError, match="inventory changed"):
        llm.run_agent("Prioritize web", database, provider=fake)


def test_step_limit_is_enforced(registered):
    database, _, _ = registered
    fake = ScriptedProvider([call("list_workloads", identifier=str(i)) for i in range(6)])
    with pytest.raises(provider.AgentError, match="six-call limit"):
        llm.run_agent("Prioritize web", database, provider=fake)
    assert len(fake.histories) == 6


def test_missing_registry_does_not_create_files(tmp_path):
    database = tmp_path / "absent" / "registry.sqlite3"
    with pytest.raises(provider.AgentError, match="Register Linux workloads"):
        llm.run_agent("Prioritize web", database, provider=ScriptedProvider([]))
    assert not database.parent.exists()


def test_all_tool_schemas_are_closed_and_required():
    for tool in tool_definitions():
        schema = tool["parameters"]
        assert tool["strict"] is True and schema["additionalProperties"] is False
        assert set(schema.get("required", [])) == set(schema["properties"])


def test_cli_outputs_json_and_readable_errors(registered, monkeypatch):
    database, _, _ = registered
    monkeypatch.setattr(llm, "create_provider", lambda **kwargs: ScriptedProvider(sequence()))
    result = CliRunner().invoke(app, ["agent", "run", "Prioritize web for ten minutes", "--db", str(database), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "evaluated"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(database.parent)
    monkeypatch.setenv("SENOS_LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "create_provider", provider.create_provider)
    result = CliRunner().invoke(app, ["agent", "run", "Prioritize web", "--db", str(database)])
    assert result.exit_code == 1 and "Set OPENAI_API_KEY" in result.output
    assert "Traceback" not in result.output
