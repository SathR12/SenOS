from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from senos.agents.cpu import CPUAgent, CPUContext
from senos.agents.monitor import monitor_intent
from senos.cli import app
from senos.contracts import Observation
from senos.intent import IntentError, IntentService
from senos.storage.monitor import MonitorError, MonitorStore
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo


class Clock:
    def __init__(self):
        self.utc = datetime.now(timezone.utc)
        self.seconds = 0.0
    def now(self):
        return self.utc
    def monotonic(self):
        return self.seconds
    def sleep(self, seconds):
        self.utc += timedelta(seconds=seconds)
        self.seconds += seconds


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, "/app.slice/" + unit)


@pytest.fixture
def setup(tmp_path):
    registry_path = tmp_path / "registry.sqlite3"
    registry = Registry(registry_path, Resolver())
    registry.register("web", "web.service")
    registry.register("build", "build.service")
    clock = Clock()
    intent = IntentService(registry).prioritize("web", 60, ("build",)).model_copy(update={"submitted_at": clock.now()})
    history = tmp_path / "monitor.sqlite3"
    calls = []
    def evaluate(original, database):
        assert original == intent and database == registry_path
        calls.append(original)
        pressure = 20 if len(calls) == 1 else 0
        clock.sleep(3)
        workloads = tuple(registry.list_workloads())
        observations = tuple(Observation(workload_id=w.workload_id, observed_at=clock.now() - timedelta(seconds=i),
            state="active", cpu_usage_percent=50, cpu_pressure_some_avg10=pressure)
            for w in workloads for i in (2, 1, 0))
        context = CPUContext(intent=original, workloads=workloads, observations=observations,
            current_cpu_weights={w.workload_id: 100 for w in workloads})
        decision = CPUAgent().evaluate(context, now=clock.now())
        return {"executed": False, "mode": "dry_run", "source": "live", "intent": original.model_dump(mode="json"),
            "context": context.model_dump(mode="json"), "warnings": [], "decision": decision.model_dump(mode="json")}
    return registry_path, history, registry, clock, intent, evaluate, calls


def run(setup, **kwargs):
    database, history, _, clock, intent, evaluate, _ = setup
    return monitor_intent(intent, database, history, evaluate=evaluate, pause=clock.sleep, now=clock.now, clock=clock.monotonic, **kwargs)


def test_repeated_local_decisions_are_persisted_without_writes_or_llm_calls(setup, monkeypatch):
    database, history, _, clock, intent, _, calls = setup
    original = database.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("Observe-only monitoring must not apply policies or call an LLM")
    monkeypatch.setattr("senos.agents.provider.OpenAIProvider.respond", forbidden)
    monkeypatch.setattr("senos.agents.gemini.GeminiProvider.respond", forbidden)
    monkeypatch.setattr("senos.executor.cpu.SystemdCPUBackend.write", forbidden)
    monkeypatch.setattr("senos.storage.changes.CPUJournal.capture", forbidden)
    events = []
    result = run(setup, max_cycles=2, on_event=events.append)
    assert result["status"] == "limited" and result["cycles"] == 2
    assert result["executed"] is False and len(calls) == 2
    stored = MonitorStore(history).events(result["run_id"])
    assert [e["evaluation"]["decision"]["status"] for e in stored] == ["proposed", "no_action"]
    assert all(e["evaluation"]["intent"] == intent.model_dump(mode="json") for e in stored)
    assert stored[0]["evaluation"]["decision"]["proposal"]["expires_at"] == (intent.submitted_at + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    assert database.read_bytes() == original


def test_expiration_does_not_extend_original_deadline(setup):
    _, history, _, clock, intent, _, calls = setup
    result = run(setup, interval=5)
    assert result["status"] == "expired"
    assert clock.seconds <= 63
    assert datetime.fromisoformat(result["deadline"]) == intent.submitted_at + timedelta(seconds=60)
    assert len(calls) > 1


def test_wall_clock_rollback_cannot_extend_lifetime(setup):
    _, _, _, clock, _, _, _ = setup
    original = clock.sleep
    def rollback(seconds):
        original(seconds)
        clock.utc -= timedelta(seconds=2 * seconds)
    clock.sleep = rollback
    result = run(setup)
    assert result["status"] == "expired" and clock.seconds <= 63


def test_external_stop_prevents_more_events_and_preserves_stop_status(setup):
    _, history, _, _, _, _, calls = setup
    def stop(event):
        MonitorStore(history).stop(event["run_id"].upper())
    result = run(setup, on_event=stop)
    assert result["status"] == "stopped" and result["cycles"] == 1
    assert len(calls) == 1
    assert len(MonitorStore(history).events(result["run_id"].upper())) == 1


def test_stop_during_collection_discards_inflight_recommendation(setup):
    database, history, _, clock, intent, evaluate, _ = setup
    identifiers = []
    def stopped(original, path):
        payload = evaluate(original, path)
        MonitorStore(history).stop(identifiers[0])
        return payload
    result = monitor_intent(intent, database, history, evaluate=stopped, pause=clock.sleep, now=clock.now,
        clock=clock.monotonic, on_start=identifiers.append)
    assert result["status"] == "stopped" and result["cycles"] == 0


def test_interrupt_finalizes_history(setup):
    def interrupted(event):
        raise KeyboardInterrupt()
    result = run(setup, on_event=interrupted)
    assert result["status"] == "interrupted" and result["cycles"] == 1


def test_collection_failure_keeps_prior_events_and_marks_failed(setup):
    database, history, _, clock, intent, evaluate, calls = setup
    def fail(original, path):
        if calls:
            raise IntentError("Target no longer registered")
        return evaluate(original, path)
    with pytest.raises(MonitorError, match="no longer registered"):
        monitor_intent(intent, database, history, evaluate=fail, pause=clock.sleep, now=clock.now, clock=clock.monotonic)
    session = MonitorStore(history).list_runs()[0]
    assert session["status"] == "failed" and session["cycles"] == 1


def test_reuses_initial_agent_evaluation_without_repeating_model_or_collection(setup):
    _, _, _, _, intent, evaluate, calls = setup
    initial = evaluate(intent, setup[0])
    result = run(setup, initial_evaluation=initial, max_cycles=1)
    assert result["cycles"] == 1 and len(calls) == 1


@pytest.mark.parametrize("options", [{"interval": float("nan")}, {"interval": 0}, {"interval": 61}, {"max_cycles": True}, {"max_cycles": 0}])
def test_invalid_monitor_options_do_not_create_history(setup, options):
    with pytest.raises(MonitorError):
        run(setup, **options)
    assert not setup[1].exists()


def test_registry_cannot_be_used_for_monitor_writes(setup):
    database, _, _, _, intent, _, _ = setup
    before = database.read_bytes()
    with pytest.raises(MonitorError, match="separate"):
        monitor_intent(intent, database, database)
    assert database.read_bytes() == before


def test_missing_history_reads_do_not_create_database(tmp_path):
    store = MonitorStore(tmp_path / "missing" / "monitor.sqlite3")
    assert store.list_runs() == []
    with pytest.raises(MonitorError, match="valid UUID"):
        store.get("invalid")
    assert not store.path.parent.exists()


def test_cli_session_inspection_and_stop(setup):
    _, history, _, _, _, _, _ = setup
    result = run(setup, max_cycles=1)
    runner = CliRunner()
    listed = runner.invoke(app, ["monitor", "list", "--monitor-db", str(history)])
    assert listed.exit_code == 0
    assert json.loads(listed.stdout)["sessions"][0]["run_id"] == result["run_id"]
    shown = runner.invoke(app, ["monitor", "show", result["run_id"], "--monitor-db", str(history)])
    assert shown.exit_code == 0 and len(json.loads(shown.stdout)["events"]) == 1
    stopped = runner.invoke(app, ["monitor", "stop", result["run_id"], "--monitor-db", str(history)])
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout)["status"] == "limited"
    assert runner.invoke(app, ["agent", "run", "test", "--max-cycles", "1"]).exit_code == 2


def test_agent_watch_calls_agent_once_and_streams_local_cycles(setup, monkeypatch):
    from senos.agents import llm, monitor
    database, history, _, clock, intent, evaluate, calls = setup
    agent_calls = []
    def agent(*args, **kwargs):
        agent_calls.append(args)
        return {"status": "evaluated", "executed": False, "mode": "dry_run", "rationale": "Selected requested web workload",
            "agent": {"model": "scripted-test", "steps": 3}, "evaluation": evaluate(intent, database)}
    monkeypatch.setattr(llm, "run_agent", agent)
    original = monitor.monitor_intent
    def local(*args, **kwargs):
        return original(*args, **kwargs, evaluate=evaluate, pause=clock.sleep, now=clock.now, clock=clock.monotonic)
    monkeypatch.setattr(monitor, "monitor_intent", local)
    result = CliRunner().invoke(app, ["agent", "run", "Prioritize web for one minute", "--watch", "--max-cycles", "2", "--interval", "1", "--db", str(database), "--monitor-db", str(history), "--json"])
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [record["event"] for record in records] == ["agent", "started", "cycle", "cycle", "finished"]
    assert records[-1]["result"]["cycles"] == 2
    assert len(agent_calls) == 1 and len(calls) == 2


def test_expiration_during_collection_discards_late_proposal(setup):
    database, history, _, clock, intent, evaluate, _ = setup
    def late(original, path):
        result = evaluate(original, path)
        clock.sleep(60)
        return result
    result = monitor_intent(intent, database, history, evaluate=late, pause=clock.sleep, now=clock.now, clock=clock.monotonic)
    assert result["status"] == "expired" and result["cycles"] == 0
    assert MonitorStore(history).events(result["run_id"]) == []


def test_monitor_refuses_foreign_initial_intent(setup):
    database, _, _, _, intent, evaluate, _ = setup
    initial = evaluate(intent, database)
    initial["intent"]["source_text"] = "different objective"
    with pytest.raises(MonitorError, match="different intent"):
        run(setup, initial_evaluation=initial)


@pytest.mark.parametrize("ending", ["limit", "interrupt", "stop"])
def test_apply_mode_restores_on_all_normal_monitor_exit_paths(setup, ending):
    from senos.contracts import ActionResult
    database, history, _, clock, intent, _, _ = setup
    class Application:
        finalized = 0
        def begin(self, objective):
            assert objective == intent
            return "test-session"
        def apply(self, session, evaluation):
            proposal = evaluation["decision"]["proposal"]
            return ActionResult(proposal_id=proposal["proposal_id"], workload_id=intent.target_workload_id,
                status="applied", requested_cpu_weight=200, previous_cpu_weight=100, effective_cpu_weight=200,
                reason="test application", started_at=clock.now(), finished_at=clock.now())
        def finish(self, session):
            self.finalized += 1
            return [{"status": "restored"}]
    application = Application()
    def callback(event):
        if ending == "interrupt":
            raise KeyboardInterrupt()
        if ending == "stop":
            MonitorStore(history).stop(event["run_id"])
    result = run(setup, max_cycles=1, apply=True, application=application, on_event=callback)
    assert result["mode"] == "apply" and result["executed"] is True
    assert result["recovery"] == [{"status": "restored"}] and application.finalized == 1
    assert result["status"] == {"limit": "limited", "interrupt": "interrupted", "stop": "stopped"}[ending]


def test_apply_mode_restore_failure_is_not_reported_as_success(setup):
    from senos.recovery.errors import RestoreError
    class Application:
        def begin(self, intent):
            return "test-session"
        def apply(self, session, evaluation):
            return None
        def finish(self, session):
            raise RestoreError("permission denied")
    with pytest.raises(MonitorError, match="restoration failed"):
        run(setup, max_cycles=1, apply=True, application=Application())
    assert MonitorStore(setup[1]).list_runs()[0]["status"] == "failed"


def test_cli_apply_requires_watch_before_any_model_call():
    result = CliRunner().invoke(app, ["agent", "run", "Prioritize web", "--apply"])
    assert result.exit_code == 2 and "requires --watch" in result.output


def test_adaptive_monitor_repeated_model_choices_replace_heuristic(setup):
    from senos.agents.adaptive import AdaptiveCPUAdvisor
    class Provider:
        model = "scripted"
        def respond(self, history, tools, instructions):
            return {"status": "completed", "output": [{"type": "function_call", "name": "recommend_cpu_weight",
                "arguments": json.dumps({"cpu_weight": 150, "rationale": "Choose intermediate share from fresh contention evidence"})}]}
    advisor = AdaptiveCPUAdvisor(provider=Provider())
    events = []
    result = run(setup, max_cycles=2, advisor=advisor, on_event=events.append)
    assert result["cycles"] == advisor.calls == 2
    assert len(setup[-1]) == 4  # Evidence is refreshed after every model response.
    assert all(event["evaluation"]["decision"]["proposal"]["proposed_cpu_weight"] == 150 for event in events)
    assert events[1]["evaluation"]["advisor"]["review"] == 2
    assert result["executed"] is False


def test_adaptive_response_after_expiration_is_discarded(setup):
    from senos.agents.adaptive import AdaptiveCPUAdvisor
    class Provider:
        model = "slow"
        def respond(self, *args):
            setup[3].sleep(61)
            return {"status": "completed", "output": [{"type": "function_call", "name": "recommend_cpu_weight",
                "arguments": json.dumps({"cpu_weight": 150, "rationale": "Late response"})}]}
    result = run(setup, advisor=AdaptiveCPUAdvisor(provider=Provider()))
    assert result["cycles"] == 0 and result["status"] == "expired"


@pytest.mark.parametrize("arguments", [
    {"cpu_weight": 201, "rationale": "Too large"},
    {"cpu_weight": True, "rationale": "Wrong type"},
    {"cpu_weight": 150, "rationale": "Extra field", "command": "anything"},
])
def test_adaptive_invalid_response_stops_monitor(setup, arguments):
    from senos.agents.adaptive import AdaptiveCPUAdvisor
    class Provider:
        model = "invalid"
        def respond(self, *args):
            return {"status": "completed", "output": [{"type": "function_call", "name": "recommend_cpu_weight",
                "arguments": json.dumps(arguments)}]}
    with pytest.raises(MonitorError, match="invalid|outside"):
        run(setup, advisor=AdaptiveCPUAdvisor(provider=Provider()))


def test_adaptive_provider_failure_restores_application_session(setup):
    from senos.agents.adaptive import AdaptiveCPUAdvisor
    from senos.agents.provider import AgentError
    class Provider:
        model = "unavailable"
        def respond(self, *args):
            raise AgentError("Provider unavailable")
    class Store:
        def get(self, identifier):
            return {"original_effective": 100, "last_change_at": None}
    class Application:
        store = Store()
        finalized = False
        def begin(self, intent):
            return "test"
        def apply_adaptive(self, *args):
            pytest.fail("No policy may be applied after a provider failure")
        def finish(self, identifier):
            self.finalized = True
            return []
    application = Application()
    with pytest.raises(MonitorError, match="Provider unavailable"):
        run(setup, apply=True, application=application, advisor=AdaptiveCPUAdvisor(provider=Provider()))
    assert application.finalized


def test_adaptive_budget_is_validated_before_starting_session(setup):
    with pytest.raises(MonitorError, match="at most 12"):
        run(setup, advisor=object(), max_cycles=13)


def test_stop_during_model_response_discards_recommendation(setup):
    from senos.agents.adaptive import AdaptiveCPUAdvisor
    class Provider:
        model = "stopped"
        def respond(self, *args):
            store = MonitorStore(setup[1])
            store.stop(store.list_runs()[0]["run_id"])
            return {"status": "completed", "output": [{"type": "function_call", "name": "recommend_cpu_weight",
                "arguments": json.dumps({"cpu_weight": 150, "rationale": "Discard after stop"})}]}
    result = run(setup, advisor=AdaptiveCPUAdvisor(provider=Provider()))
    assert result["status"] == "stopped" and result["cycles"] == 0
