import json

import pytest
from typer.testing import CliRunner

from senos.cli import app
from senos.contracts import Intent, IntentKind
from senos.intent import IntentError, IntentService
from senos.workloads.errors import RegistrationError
from senos.workloads.registry import Registry
from senos.workloads.systemd import SystemdResolver, UnitInfo


class Resolver:
    def resolve(self, unit, manager):
        return UnitInfo(unit=unit, cgroup="/user.slice/" + unit)


@pytest.fixture
def registered(tmp_path):
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database, Resolver())
    editor, _ = registry.register("editor", "editor.service")
    build, _ = registry.register("build", "build.service")
    backup, _ = registry.register("backup", "backup.service")
    return database, registry, editor, build, backup


def test_service_resolves_names_to_stable_workload_ids(registered):
    _, registry, editor, build, backup = registered

    intent = IntentService(registry).prioritize(
        "editor", 600, ("build", "backup")
    )

    assert intent.kind is IntentKind.PRIORITIZE
    assert intent.target_workload_id == editor.workload_id
    assert intent.preserve_workload_ids == (build.workload_id, backup.workload_id)
    assert intent.duration_seconds == 600
    assert intent.source_text == (
        "prioritize editor for 600 seconds; preserve build, backup"
    )


def test_registry_lookup_supports_name_and_uuid(registered):
    _, registry, editor, _, _ = registered

    assert registry.get_by_name("editor") == editor
    assert registry.get_by_id(editor.workload_id) == editor
    assert registry.get_by_id(str(editor.workload_id)) == editor

    with pytest.raises(RegistrationError, match="valid UUID"):
        registry.get_by_id("not-a-uuid")


def test_cli_outputs_validated_intent_json_without_execution(registered):
    database, _, editor, build, _ = registered

    result = CliRunner().invoke(app, [
        "intent",
        "prioritize",
        "editor",
        "--duration-seconds",
        "600",
        "--preserve",
        "build",
        "--db",
        str(database),
        "--json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accepted"] is True
    assert payload["executed"] is False
    intent = Intent.model_validate(payload["intent"])
    assert intent.target_workload_id == editor.workload_id
    assert intent.preserve_workload_ids == (build.workload_id,)


def test_cli_text_is_explicit_that_no_policy_was_applied(registered):
    database, _, _, _, _ = registered

    result = CliRunner().invoke(app, [
        "intent",
        "prioritize",
        "editor",
        "--duration-seconds",
        "60",
        "--preserve",
        "build",
        "--preserve",
        "backup",
        "--db",
        str(database),
    ])

    assert result.exit_code == 0, result.output
    assert "Validated intent: prioritize editor for 60 seconds" in result.output
    assert "Preserve: build, backup" in result.output
    assert "No resource policy was applied" in result.output


@pytest.mark.parametrize("duration", ["0", "86401"])
def test_cli_rejects_out_of_range_duration(registered, duration):
    database, _, _, _, _ = registered

    result = CliRunner().invoke(app, [
        "intent",
        "prioritize",
        "editor",
        "--duration-seconds",
        duration,
        "--db",
        str(database),
    ])

    assert result.exit_code == 1
    assert "senos intent:" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("target", "preserved", "message"),
    [
        ("missing", (), "not registered"),
        ("editor", ("missing",), "not registered"),
        ("editor", ("build", "build"), "must not contain duplicates"),
        ("editor", ("editor",), "cannot also be a preserved workload"),
    ],
)
def test_service_rejects_unknown_or_conflicting_workloads(
    registered, target, preserved, message
):
    _, registry, _, _, _ = registered

    with pytest.raises(IntentError, match=message):
        IntentService(registry).prioritize(target, 60, preserved)


def test_intent_lookup_never_contacts_systemd(registered, monkeypatch):
    database, _, _, _, _ = registered

    def fail(*args, **kwargs):
        pytest.fail("Intent lookup must not resolve the unit through systemd")

    monkeypatch.setattr(SystemdResolver, "resolve", fail)
    result = CliRunner().invoke(app, [
        "intent",
        "prioritize",
        "editor",
        "--duration-seconds",
        "60",
        "--db",
        str(database),
    ])

    assert result.exit_code == 0, result.output


def test_intent_honors_registry_environment_variable(registered):
    database, _, _, _, _ = registered

    result = CliRunner().invoke(
        app,
        ["intent", "prioritize", "editor", "--duration-seconds", "60"],
        env={"SENOS_REGISTRY_PATH": str(database)},
    )

    assert result.exit_code == 0, result.output


def test_missing_registry_is_readable_and_not_created(tmp_path):
    database = tmp_path / "missing" / "registry.sqlite3"

    result = CliRunner().invoke(app, [
        "intent",
        "prioritize",
        "editor",
        "--duration-seconds",
        "60",
        "--db",
        str(database),
    ])

    assert result.exit_code == 1
    assert "not registered" in result.output
    assert "Traceback" not in result.output
    assert not database.parent.exists()
