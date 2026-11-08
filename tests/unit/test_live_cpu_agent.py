from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.agents.cpu import CPUAgent
from senos.agents.live import collect_live_context
from senos.contracts import Observation
from senos.telemetry.linux import CPUWeightReading
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
