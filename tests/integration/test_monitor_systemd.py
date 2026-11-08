"""Observe-only monitoring of real temporary Linux services, without API calls."""

from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from senos.storage.monitor import MonitorStore
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.registry import Registry

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 in an isolated Linux systemd user session",
)


def test_monitor_cli_repeats_and_persists_without_changing_workloads(tmp_path):
    units = [f"senos-monitor-test-{uuid4().hex}.service" for _ in range(2)]
    database, history = tmp_path / "registry.sqlite3", tmp_path / "monitor.sqlite3"
    registry = Registry(database)
    workloads = []
    try:
        for name, unit in zip(("web", "build"), units):
            subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", "--property=RuntimeMaxSec=120", "--property=CPUWeight=100", "/usr/bin/sleep", "90"], capture_output=True, check=True, timeout=10)
            workload, _ = registry.register(name, unit)
            workloads.append(workload)
        telemetry = LinuxTelemetry()
        before = [telemetry.read_cpu_weight(workload) for workload in workloads]
        original = database.read_bytes()
        command = [sys.executable, "-c", "from senos.cli import app; app()", "monitor", "start", "web", "--duration-seconds", "60", "--preserve", "build", "--interval", "1", "--max-cycles", "2", "--db", str(database), "--monitor-db", str(history), "--json"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        records = [json.loads(line) for line in completed.stdout.splitlines()]
        assert [record["event"] for record in records] == ["started", "cycle", "cycle", "finished"]
        session = records[-1]["result"]
        assert session["status"] == "limited" and session["executed"] is False
        events = MonitorStore(history).events(session["run_id"])
        assert len(events) == 2
        assert all(event["evaluation"]["intent"] == session["intent"] for event in events)
        deadline = datetime.fromisoformat(session["intent"]["submitted_at"].replace("Z", "+00:00")) + timedelta(seconds=60)
        assert datetime.fromisoformat(session["deadline"]) == deadline
        assert [telemetry.read_cpu_weight(workload) for workload in workloads] == before
        assert database.read_bytes() == original
    finally:
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", "--", unit], capture_output=True, timeout=10)
