"""Opt-in Linux test: launches and removes its own temporary sleep service."""

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


def test_register_real_user_service_across_cli_processes(tmp_path):
    unit = f"senos-register-test-{uuid4().hex}.service"
    database = tmp_path / "registry.sqlite3"
    try:
        subprocess.run(
            ["systemd-run", "--user", "--unit", unit, "--collect", "/usr/bin/sleep", "60"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        command = [sys.executable, "-c", "from senos.cli import app; app()", "register", "test-workload", "--unit", unit, "--db", str(database), "--json"]
        first = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        second = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        initial = json.loads(first.stdout)
        repeated = json.loads(second.stdout)
        assert initial["created"] is True
        assert initial["workload"]["unit"] == unit
        assert repeated["created"] is False
        assert repeated["workload"] == initial["workload"]
        status_command = [sys.executable, "-c", "from senos.cli import app; app()", "status", "--db", str(database), "--json"]
        status = json.loads(subprocess.run(status_command, check=True, capture_output=True, text=True, timeout=10).stdout)
        assert status["source"] == "registry"
        assert status["workloads"][0]["workload"]["workload_id"] == initial["workload"]["id"]
        assert status["workloads"][0]["observation"]["cpu_usage_percent"] is None
        remove_command = [sys.executable, "-c", "from senos.cli import app; app()", "unregister", "test-workload", "--db", str(database), "--json"]
        removed = subprocess.run(remove_command, check=True, capture_output=True, text=True, timeout=10)
        assert json.loads(removed.stdout) == {"removed": 1}
        empty = subprocess.run(status_command, check=True, capture_output=True, text=True, timeout=10)
        assert json.loads(empty.stdout)["workloads"] == []
        # Removal affects the registry only, not the running application.
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", "--", unit], check=True, timeout=10)
    finally:
        subprocess.run(["systemctl", "--user", "stop", "--", unit], check=False, capture_output=True, timeout=10)
