"""Opt-in smoke benchmark using only temporary synthetic services."""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 in an isolated Linux systemd user session",
)


def test_real_benchmark_records_measurements_and_cleans_up(tmp_path):
    output = tmp_path / "results"
    command = [sys.executable, "-m", "benchmarks.scenarios.cpu_contention", "--output", str(output),
        "--seconds", "1", "--warmup", "0.2", "--repetitions", "1", "--web-iterations", "1000"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((output / "result.json").read_text())
    assert payload["status"] == "completed" and payload["cleanup_errors"] == []
    phases = {phase["phase"]: phase for phase in payload["phases"]}
    assert len(phases) == 3
    assert phases["interactive_only"]["requests"]["successes"] > 0
    assert phases["background_only"]["background_batches_per_second"] > 0
    dry_run = phases["contended_dry_run"]
    assert dry_run["requests"]["successes"] > 0
    assert dry_run["evaluation"]["payload"]["executed"] is False
    assert all(phase["weights_before"] == phase["weights_after"] for phase in phases.values())
    for unit in payload["launched_service_units"]:
        state = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10)
        assert state.stdout.strip() not in {"active", "activating", "deactivating"}
