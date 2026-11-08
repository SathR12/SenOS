from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.agents.cpu import CPUAgent
from senos.agents.live import collect_live_context
from senos.contracts import Observation
from senos.telemetry.linux import CPUWeightReading
from senos.storage.persistence import PolicyStore
from senos.workloads.registry import Registry
from senos.workloads.systemd import UnitInfo

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit, "/user.slice/app.slice/" + unit)


class Collector:
    def __init__(self):
        self.now = NOW
        self.rounds = 0
        self.restarted = False
        self.sibling = True
        self.weight = 100
        self.pressure = 20
        self.drop = False

    def read_cpu_weight(self, workload):
        invocation = "b" * 32 if self.restarted and self.rounds else "a" * 32
        parent = "app.slice" if self.sibling or workload.name == "web" else "session.slice"
        weight = self.weight if self.rounds else 100
        return CPUWeightReading(weight, invocation, Path("/cgroup/user.slice") / parent / workload.unit)

    def observe_all(self, workloads):
        self.rounds += 1
        self.now += timedelta(seconds=0.5)
        return [Observation(workload_id=workload.workload_id, observed_at=self.now, state="active", cpu_usage_percent=70, cpu_pressure_some_avg10=self.pressure) for workload in workloads if not self.drop]

    def pause(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    registry.register("web", "web.service")
    registry.register("build", "build.service")
    return database, Collector()


def collect(database, telemetry, **options):
    return collect_live_context(database, "web", preserve_names=("build",), telemetry=telemetry, pause=telemetry.pause, utc_now=lambda: telemetry.now, **options)


def test_collects_live_history_and_known_weights_for_a_proposal(setup):
    database, telemetry = setup
    before = database.read_bytes()
    context, warnings = collect(database, telemetry)
    decision = CPUAgent().evaluate(context, now=telemetry.now)
    assert telemetry.rounds == 3
    assert len(context.observations) == 6
    assert decision.status == "proposed"
    assert decision.proposal.proposed_cpu_weight == 200
    assert warnings == ()
    assert database.read_bytes() == before


@pytest.mark.parametrize("change", ["restart", "weight", "missing_samples"])
def test_changed_or_missing_evidence_does_not_propose(setup, change):
    database, telemetry = setup
    telemetry.restarted = change == "restart"
    telemetry.weight = 300 if change == "weight" else 100
    telemetry.drop = change == "missing_samples"
    context, warnings = collect(database, telemetry)
    assert CPUAgent().evaluate(context, now=telemetry.now).status == "no_action"
    if change != "missing_samples":
        assert warnings


def test_different_scheduling_parent_is_not_a_competing_peer(setup):
    database, telemetry = setup
    telemetry.sibling = False
    context, warnings = collect(database, telemetry)
    assert all(weight == 100 for weight in context.current_cpu_weights.values())
    assert context.eligible_cpu_peer_ids == ()
    assert CPUAgent().evaluate(context, now=telemetry.now).status == "no_action"


def test_low_pressure_correctly_returns_no_action(setup):
    database, telemetry = setup
    telemetry.pressure = 0
    context, warnings = collect(database, telemetry)
    assert CPUAgent().evaluate(context, now=telemetry.now).status == "no_action"


@pytest.mark.parametrize("target,preserved", [("missing", ()), ("web", ("missing",)), ("web", ("web",)), ("web", ("build", "build"))])
def test_invalid_target_selection_fails_before_sampling(setup, target, preserved):
    database, telemetry = setup
    with pytest.raises(ValueError):
        collect_live_context(database, target, preserve_names=preserved, telemetry=telemetry, pause=telemetry.pause, utc_now=lambda: telemetry.now)
    assert telemetry.rounds == 0


def test_missing_registry_does_not_get_created(tmp_path):
    database = tmp_path / "absent" / "registry.sqlite3"
    with pytest.raises(ValueError, match="not registered"):
        collect_live_context(database, "web", telemetry=Collector())
    assert not database.parent.exists()


def test_unsupported_environment_is_explicit(setup, monkeypatch):
    database, telemetry = setup
    monkeypatch.setattr("senos.agents.live.default_telemetry", lambda: None)
    with pytest.raises(ValueError, match="requires Linux"):
        collect_live_context(database, "web")


def test_cli_target_mode_does_not_accept_another_source():
    runner = CliRunner()
    assert runner.invoke(app, ["agent", "cpu", "--target", "web", "--demo"]).exit_code == 2
    assert runner.invoke(app, ["agent", "cpu", "--preserve", "build", "--demo"]).exit_code == 2
    assert runner.invoke(app, ["agent", "cpu", "--target", "web", "--duration", "0"]).exit_code == 2


def test_registration_changes_during_sampling_abort_context(setup):
    database, telemetry = setup
    observe = telemetry.observe_all
    def remove_during_collection(workloads):
        result = observe(workloads)
        if telemetry.rounds == 1:
            Registry(database).unregister("build")
        return result
    telemetry.observe_all = remove_during_collection
    with pytest.raises(ValueError, match="inventory changed"):
        collect(database, telemetry)


def test_cli_live_mode_renders_evidence_and_passes_selection(monkeypatch, tmp_path):
    from senos.agents.demo import cpu_context
    from senos.agents import runner
    calls = []
    def collect(database, target, **options):
        calls.append((database, target, options))
        return cpu_context(), ("example collection note",)
    monkeypatch.setattr(runner, "collect_live_context", collect)
    result = CliRunner().invoke(app, ["agent", "cpu", "--target", "web", "--preserve", "build", "--duration", "300", "--db", str(tmp_path / "registry.sqlite3")])
    assert result.exit_code == 0, result.output
    assert "DRY RUN (live): proposed" in result.output
    assert "samples=3" in result.output
    assert "PSI=15.0%" in result.output
    assert calls[0][1] == "web"
    assert calls[0][2] == {"preserve_names": ("build",), "duration_seconds": 300}


@pytest.mark.parametrize("pressure,status", [(20, "proposed"), (0, "no_action")])
def test_intent_pipeline_cli_preserves_identity_and_never_writes(setup, monkeypatch, pressure, status):
    import json
    from senos.intent import IntentService
    from senos.intent import pipeline

    database, telemetry = setup
    telemetry.pressure = pressure
    original = IntentService.prioritize
    captured = []

    def prioritize(service, *args, **kwargs):
        intent = original(service, *args, **kwargs).model_copy(update={"submitted_at": NOW})
        captured.append(intent)
        return intent

    def collect(database, *, intent):
        return collect_live_context(database, intent=intent, telemetry=telemetry, pause=telemetry.pause)

    class Clock:
        @staticmethod
        def now(tz):
            return telemetry.now

    def forbidden(*args, **kwargs):
        pytest.fail("Dry-run pipeline must not write policies or recovery records")

    monkeypatch.setattr(IntentService, "prioritize", prioritize)
    monkeypatch.setattr(pipeline, "collect_live_context", collect)
    monkeypatch.setattr(pipeline, "datetime", Clock)
    monkeypatch.setattr("senos.executor.cpu.SystemdCPUBackend.write", forbidden)
    monkeypatch.setattr("senos.storage.changes.CPUJournal.capture", forbidden)
    result = CliRunner().invoke(app, ["intent", "prioritize", "web", "--duration-seconds", "600", "--preserve", "build", "--evaluate", "--json", "--db", str(database)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["executed"] is False and payload["mode"] == "dry_run"
    assert payload["intent"] == payload["context"]["intent"] == captured[0].model_dump(mode="json")
    assert payload["decision"]["status"] == status
    if status == "proposed":
        proposal = payload["decision"]["proposal"]
        assert proposal["intent_id"] == str(captured[0].intent_id)
        assert proposal["workload_id"] == str(captured[0].target_workload_id)
        assert datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00")) == NOW + timedelta(seconds=600)
    assert PolicyStore(database).get_intent(captured[0].intent_id).model_dump(mode="json") == captured[0].model_dump(mode="json")


@pytest.mark.parametrize("name", ["web", "build"])
def test_existing_intent_rejects_replaced_workload_identity(setup, name):
    from senos.intent import IntentService

    database, telemetry = setup
    registry = Registry(database, Resolver())
    intent = IntentService(registry).prioritize("web", 600, ("build",))
    registry.unregister(name)
    registry.register(name, name + ".service")
    with pytest.raises(ValueError, match="no longer registered"):
        collect_live_context(database, intent=intent, telemetry=telemetry)
    assert telemetry.rounds == 0


def test_supplied_intent_deadline_is_not_reset_by_sampling(setup):
    from senos.intent import IntentService

    database, telemetry = setup
    intent = IntentService(Registry(database)).prioritize("web", 1).model_copy(update={"submitted_at": NOW})
    context, _ = collect_live_context(database, intent=intent, telemetry=telemetry, pause=telemetry.pause)
    assert context.intent == intent
    decision = CPUAgent().evaluate(context, now=telemetry.now)
    assert decision.status == "no_action" and "expired" in decision.reason


def test_intent_evaluation_on_unsupported_host_has_readable_error(setup, monkeypatch):
    database, _ = setup
    monkeypatch.setattr("senos.agents.live.default_telemetry", lambda: None)
    result = CliRunner().invoke(app, ["intent", "prioritize", "web", "--duration-seconds", "600", "--evaluate", "--db", str(database)])
    assert result.exit_code == 1
    assert "senos intent:" in result.output and "requires Linux" in result.output
    assert "Traceback" not in result.output


def test_supplied_intent_cannot_be_overridden_by_names(setup):
    from senos.intent import IntentService

    database, telemetry = setup
    intent = IntentService(Registry(database)).prioritize("web", 600)
    with pytest.raises(ValueError, match="cannot be combined"):
        collect_live_context(database, "build", intent=intent, telemetry=telemetry)
    assert telemetry.rounds == 0


@pytest.mark.parametrize("status", ["proposed", "no_action"])
def test_intent_evaluation_text_output_is_explicitly_dry_run(setup, monkeypatch, status):
    from senos.agents.demo import cpu_context
    from senos.intent import pipeline

    database, _ = setup
    decision = CPUAgent().evaluate(cpu_context()).model_dump(mode="json")
    if status == "no_action":
        decision = {"status": "no_action", "reason": "No sustained pressure", "proposal": None}
    monkeypatch.setattr(pipeline, "evaluate_intent", lambda intent, database: {"warnings": ["collection note"], "decision": decision})
    result = CliRunner().invoke(app, ["intent", "prioritize", "web", "--duration-seconds", "600", "--evaluate", "--db", str(database)])
    assert result.exit_code == 0, result.output
    assert f"DRY RUN (live): {status}" in result.output
    assert "Note: collection note" in result.output
    assert ("Would set CPUWeight" in result.output) == (status == "proposed")
    assert result.output.rstrip().endswith("No resource policy was applied.")


def test_restore_intent_is_rejected_before_cpu_collection(setup):
    from senos.contracts import Intent

    database, telemetry = setup
    target = Registry(database).get_by_name("web")
    intent = Intent(kind="restore", target_workload_id=target.workload_id, source_text="restore web")
    with pytest.raises(ValueError, match="Only prioritize"):
        collect_live_context(database, intent=intent, telemetry=telemetry)
    assert telemetry.rounds == 0
