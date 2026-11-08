"""Real application, normal recovery, and SIGKILL recovery on owned test units."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from senos.agents.live import collect_live_context
from senos.contracts import Intent
from senos.executor.application import CPUApplication
from senos.executor.cpu import SystemdCPUBackend
from senos.intent import IntentService
from senos.storage.changes import CPUJournal
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.registry import Registry

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SENOS_RUN_SYSTEMD_TESTS") != "1",
    reason="Set SENOS_RUN_SYSTEMD_TESTS=1 inside an isolated Linux systemd user session",
)


def test_real_cpu_application_normal_restore_and_controller_crash_recovery(tmp_path):
    units = [f"senos-apply-test-{uuid4().hex}.service" for _ in range(2)]
    database, history = tmp_path / "registry.sqlite3", tmp_path / "monitor.sqlite3"
    registry = Registry(database)
    workloads = []
    process = None
    application = None
    session = None
    try:
        cpu = min(os.sched_getaffinity(0))
        for name, unit in zip(("web", "build"), units):
            subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", "--property=CPUWeight=100", "--property=RuntimeMaxSec=180", "/usr/bin/taskset", "-c", str(cpu), sys.executable, "-c", "while True: pass"], check=True, capture_output=True, timeout=10)
            workload, _ = registry.register(name, unit)
            workloads.append(workload)
        telemetry, backend = LinuxTelemetry(), SystemdCPUBackend()
        if any(telemetry.read_cpu_weight(workload).weight is None for workload in workloads):
            pytest.skip("Effective CPU weights unavailable; cannot verify real application")
        time.sleep(10)
        intent = IntentService(registry).prioritize("web", 60, ("build",))
        application = CPUApplication(database)
        session = application.begin(intent)
        context, _ = collect_live_context(database, intent=intent)
        action = application.apply(session, {"context": context.model_dump(mode="json")})
        assert action is not None and action.status.value == "applied", action
        assert backend.read(workloads[0]).weight == 200
        assert telemetry.read_cpu_weight(workloads[0]).weight == 200
        assert backend.read(workloads[1]).weight == 100
        application.finish(session)
        assert backend.read(workloads[0]).weight == 100
        assert CPUJournal(database).pending() == []
        # The independent worker also expires a change while its controller stays alive.
        short_intent = IntentService(registry).prioritize("web", 12, ("build",))
        short_session = application.begin(short_intent)
        short_context, _ = collect_live_context(database, intent=short_intent)
        short_action = application.apply(short_session, {"context": short_context.model_dump(mode="json")})
        assert short_action is not None and short_action.status.value == "applied"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if backend.read(workloads[0]).weight == 100 and CPUJournal(database).pending() == []:
                break
            time.sleep(0.25)
        assert backend.read(workloads[0]).weight == 100
        assert application.store.get(short_session)["status"] == "restored"
        assert CPUJournal(database).pending() == []
        # Exercise the real CLI, then kill it without allowing its finally block.
        args = [sys.executable, "-u", "-c", "from senos.cli import app; app()", "monitor", "start", "web", "--duration-seconds", "60", "--preserve", "build", "--interval", "1", "--apply", "--db", str(database), "--monitor-db", str(history), "--json"]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buffer = b""
        applied = False
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline and process.poll() is None:
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if not ready:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                event = json.loads(line)
                if event.get("event") == "cycle":
                    action = event["evaluation"].get("execution")
                    applied = bool(action and action["status"] == "applied")
            if applied:
                break
        assert applied, "Applying CLI never reported a verified CPU change"
        assert backend.read(workloads[0]).weight == 200
        process.kill()
        process.wait(timeout=10)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if backend.read(workloads[0]).weight == 100 and CPUJournal(database).pending() == []:
                break
            time.sleep(0.25)
        assert backend.read(workloads[0]).weight == 100
        assert telemetry.read_cpu_weight(workloads[0]).weight == 100
        assert CPUJournal(database).pending() == []
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if application is not None and session is not None:
            application.finish(session)
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", "--", unit], capture_output=True, timeout=10)
