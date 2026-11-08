"""Opt-in recovery against a service created exclusively by this test."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from senos.executor.cpu import SystemdCPUBackend
from senos.recovery.restore import RestoreService
from senos.workloads.registry import Registry

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 in an isolated Linux systemd user session",
)


@pytest.mark.parametrize("original", [100, None])
def test_real_restore_and_dry_run_preserve_registration(tmp_path, original):
    unit = f"senos-restore-test-{uuid4().hex}.service"
    database = tmp_path / "registry.sqlite3"
    try:
        command = ["systemd-run", "--user", "--unit", unit, "--collect"]
        if original is not None:
            command += ["--property=CPUWeight=" + str(original)]
        command += ["/usr/bin/sleep", "120"]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        registry = Registry(database)
        record, _ = registry.register("test", unit)
        workload = record.to_workload()
        backend = SystemdCPUBackend()
        assert backend.read(workload).weight == original
        # This is the integration hook a future executor must invoke before its write.
        RestoreService(database).capture("test")
        backend.write(workload, 300)
        assert backend.read(workload).weight == 300
        cli = [sys.executable, "-c", "from senos.cli import app; app()", "restore", "test", "--db", str(database), "--json"]
        dry_run = subprocess.run(cli + ["--dry-run"], check=True, capture_output=True, text=True, timeout=20)
        assert json.loads(dry_run.stdout)["results"][0]["status"] == "dry_run"
        assert backend.read(workload).weight == 300
        restored = subprocess.run(cli, check=True, capture_output=True, text=True, timeout=20)
        assert json.loads(restored.stdout)["results"][0]["status"] == "restored"
        assert backend.read(workload).weight == original
        assert Registry(database).list_workloads()[0].id == record.id
        repeated = subprocess.run(cli, check=True, capture_output=True, text=True, timeout=20)
        assert json.loads(repeated.stdout)["results"] == []
    finally:
        subprocess.run(["systemctl", "--user", "stop", "--", unit], check=False, capture_output=True, timeout=10)
