"""Opt-in end-to-end live proposal test; it never applies a policy."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from senos.executor.cpu import SystemdCPUBackend
from senos.workloads.registry import Registry

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 in an isolated Linux systemd user session",
)


def test_live_cpu_command_is_read_only_and_uses_real_workloads(tmp_path):
    units = [f"senos-agent-test-{uuid4().hex}.service" for _ in range(2)]
    database = tmp_path / "registry.sqlite3"
    registry = Registry(database)
    records = []
    try:
        for name, unit in zip(("target", "background"), units):
            subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", "--property=CPUWeight=100", "/usr/bin/sleep", "120"], check=True, capture_output=True, text=True, timeout=10)
            record, _ = registry.register(name, unit)
            records.append(record)
        before_database = database.read_bytes()
        backend = SystemdCPUBackend()
        before = [backend.read(record) for record in records]
        command = [sys.executable, "-c", "from senos.cli import app; app()", "agent", "cpu", "--target", "target", "--preserve", "background", "--db", str(database), "--json"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=40)
        payload = json.loads(result.stdout)
        assert payload["mode"] == "dry_run" and payload["source"] == "live"
        assert payload["decision"]["status"] == "no_action"
        assert payload["context"]["intent"]["target_workload_id"] == str(records[0].workload_id)
        assert payload["context"]["current_cpu_weights"][str(records[0].workload_id)] == 100
        assert len(payload["context"]["observations"]) == 6
        assert [backend.read(record) for record in records] == before
        assert database.read_bytes() == before_database
    finally:
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", "--", unit], check=False, capture_output=True, timeout=10)
