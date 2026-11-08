"""Explicitly opt-in, billable API + Linux systemd smoke test."""

import os
import subprocess
import sys
from uuid import uuid4

import pytest

from senos.agents.llm import run_agent
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.registry import Registry

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1"
    or os.environ.get("SENOS_RUN_LLM_TESTS") != "1",
    reason="Requires Linux, provider credentials, SENOS_RUN_SYSTEMD_TESTS=1 and SENOS_RUN_LLM_TESTS=1; spends API credits",
)


def test_live_agent_reads_registered_workloads_without_changing_settings(tmp_path):
    units = [f"senos-llm-test-{uuid4().hex}.service" for _ in range(2)]
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database)
    workloads = []
    try:
        for name, unit in zip(("web", "build"), units):
            subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", "--property=RuntimeMaxSec=300", "--property=CPUWeight=100", "/usr/bin/sleep", "240"], capture_output=True, check=True, timeout=10)
            workload, _ = registry.register(name, unit)
            workloads.append(workload)
        telemetry = LinuxTelemetry()
        before = [telemetry.read_cpu_weight(workload) for workload in workloads]
        original = database.read_bytes()
        result = run_agent("Prioritize web for 10 minutes while preserving build. Only evaluate CPU priority; do not execute anything.", database)
        assert result["status"] == "evaluated" and result["executed"] is False
        intent = result["evaluation"]["intent"]
        assert intent["target_workload_id"] == str(workloads[0].workload_id)
        assert intent["duration_seconds"] == 600
        assert intent["preserve_workload_ids"] == [str(workloads[1].workload_id)]
        assert result["evaluation"]["decision"]["status"] == "no_action"
        assert [telemetry.read_cpu_weight(workload) for workload in workloads] == before
        assert database.read_bytes() == original
    finally:
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", "--", unit], capture_output=True, check=False, timeout=10)
