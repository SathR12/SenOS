"""Opt-in Linux test: measures its own temporary busy-loop service, then stops it."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 inside a Linux systemd user session",
)

CLI = [sys.executable, "-c", "from senos.cli import app; app()"]


def test_status_reports_live_cpu_and_memory_for_a_busy_unit(tmp_path):
    unit = f"senos-telemetry-test-{uuid4().hex}.service"
    database = tmp_path / "registry.sqlite3"
    try:
        subprocess.run(
            ["systemd-run", "--user", "--unit", unit, "--collect", "/bin/sh", "-c", "while :; do :; done"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run([*CLI, "register", "busy", "--unit", unit, "--db", str(database)], check=True, capture_output=True, text=True, timeout=10)
        result = subprocess.run([*CLI, "status", "--json", "--db", str(database)], check=True, capture_output=True, text=True, timeout=10)
        payload = json.loads(result.stdout)
        assert payload["live_telemetry"] is True
        observation = payload["workloads"][0]["observation"]
        assert observation["state"] == "active"
        # A busy loop should occupy most of one logical CPU.
        assert observation["cpu_usage_percent"] > 50
        assert observation["memory_usage_bytes"] > 0

        subprocess.run(["systemctl", "--user", "stop", "--", unit], check=True, capture_output=True, timeout=10)
        stopped = json.loads(subprocess.run([*CLI, "status", "--json", "--db", str(database)], check=True, capture_output=True, text=True, timeout=10).stdout)
        assert stopped["workloads"][0]["observation"]["state"] == "inactive"
        assert stopped["workloads"][0]["observation"]["cpu_usage_percent"] is None
    finally:
        subprocess.run(["systemctl", "--user", "stop", "--", unit], check=False, capture_output=True, timeout=10)
