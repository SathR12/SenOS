"""Run an isolated, observation-only Linux CPU contention experiment."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from benchmarks.analysis.report import markdown, summarize_requests
from senos.intent import IntentService
from senos.intent.pipeline import evaluate_intent
from senos.telemetry.linux import LinuxTelemetry
from senos.workloads.registry import Registry

WORKLOAD = Path(__file__).resolve().parents[1] / "workloads" / "cpu.py"
PHASES = ("interactive_only", "background_only", "contended_dry_run")


def command(arguments: list[str], *, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=10)


def preflight(cpu: int | None) -> int:
    if sys.platform != "linux" or not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise ValueError("Run inside Linux with cgroup v2 and a systemd user session")
    for executable in ("systemctl", "systemd-run", "taskset"):
        if shutil.which(executable) is None:
            raise ValueError(f"Required command is unavailable: {executable}")
    command(["systemctl", "--user", "show-environment"])
    allowed = sorted(os.sched_getaffinity(0))
    selected = allowed[0] if cpu is None else cpu
    if selected not in allowed:
        raise ValueError(f"CPU {selected} is outside the allowed affinity {allowed}")
    return selected


def environment(cpu: int) -> dict:
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "kernel": platform.release(), "python": platform.python_version(),
        "platform": platform.platform(), "wsl": "microsoft" in platform.release().lower(),
        "logical_cpus": os.cpu_count(), "allowed_cpus": sorted(os.sched_getaffinity(0)),
        "workload_cpu": cpu, "systemd": command(["systemctl", "--version"]).stdout.splitlines()[0],
        "meminfo": Path("/proc/meminfo").read_text(),
        "cpu_controllers": Path("/sys/fs/cgroup/cgroup.controllers").read_text().strip(),
    }


def host_snapshot() -> dict:
    def read(path):
        try:
            return Path(path).read_text()
        except OSError:
            return None
    return {"loadavg": read("/proc/loadavg"), "cpu_stat": read("/proc/stat"),
        "cpu_pressure": read("/proc/pressure/cpu"), "memory_pressure": read("/proc/pressure/memory")}


def wait_for_state(path: Path, unit: str, seconds=15) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text())
        active = command(["systemctl", "--user", "is-active", unit], check=False)
        if active.stdout.strip() not in {"active", "activating"}:
            raise RuntimeError(f"Benchmark service failed to start: {unit}; inspect journalctl --user -u {unit}")
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for benchmark service {unit}")


def request(port: int, timeout: float) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/work")
        response = connection.getresponse()
        return response.status == 200 and response.read() == b"ok\n"
    finally:
        connection.close()


def measure_requests(port: int, duration: float, rate: float, timeout: float) -> tuple[list[dict], float, int]:
    """One client; arrivals missed during slow requests are counted, never hidden."""
    started = time.monotonic()
    deadline = started + duration
    index = 0
    offered = math.ceil(duration * rate)
    interval = 1 / rate
    missed = 0
    samples = []
    while index < offered:
        due = started + index * interval
        now = time.monotonic()
        if now >= deadline:
            missed += offered - index
            break
        if now < due:
            time.sleep(due - now)
        sent = time.monotonic()
        if sent >= deadline:
            missed += offered - index
            break
        error = None
        try:
            ok = request(port, min(timeout, max(0.01, deadline - sent)))
            if not ok:
                error = "unexpected_response"
        except (OSError, http.client.HTTPException) as failure:
            ok = False
            error = type(failure).__name__
        finished = time.monotonic()
        samples.append({"offset_seconds": sent - started, "latency_ms": (finished - sent) * 1000,
            "ok": ok, "error": error})
        index += 1
        due = started + index * interval
        # No burst of catch-up traffic: report the omitted offered arrivals.
        skipped = min(offered - index, max(0, math.ceil((min(finished, deadline) - due) / interval)))
        missed += skipped
        index += skipped
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    return samples, time.monotonic() - started, missed


def progress(path: Path) -> dict:
    value = json.loads(path.read_text())
    if type(value["completed_batches"]) is not int or value["completed_batches"] < 0:
        raise ValueError("Invalid benchmark progress counter")
    return value


def throughput(before: dict, after: dict) -> float:
    elapsed = after["monotonic"] - before["monotonic"]
    count = after["completed_batches"] - before["completed_batches"]
    if elapsed <= 0 or count < 0:
        raise ValueError("Background progress did not advance; trial is invalid")
    return count / elapsed


class Services:
    """Own only uniquely named test services; never touch registered user apps."""
    def __init__(self, cpu: int, runtime: int):
        self.cpu = cpu
        self.runtime = runtime
        self.owned = set()
        self.launched = []

    def start(self, mode: str, state: Path, iterations: int) -> str:
        unit = f"senos-bench-{uuid4().hex}.service"
        self.owned.add(unit)
        self.launched.append(unit)
        command(["systemd-run", "--user", "--unit", unit, "--collect",
            f"--property=RuntimeMaxSec={self.runtime}", "--property=CPUWeight=100",
            "--property=CPUAccounting=yes", "--property=MemoryAccounting=yes",
            shutil.which("taskset"), "-c", str(self.cpu), sys.executable, str(WORKLOAD),
            mode, "--state", str(state), "--iterations", str(iterations)])
        return unit

    def stop(self, unit: str) -> None:
        if unit not in self.owned:
            raise ValueError("Refusing to stop a service not owned by this run")
        command(["systemctl", "--user", "stop", "--", unit])
        self.owned.remove(unit)

    def cleanup(self) -> list[str]:
        errors = []
        for unit in tuple(self.owned):
            try:
                self.stop(unit)
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(f"Could not stop {unit}: {error}")
        return errors


def cpu_time() -> float:
    import resource

    return sum(usage.ru_utime + usage.ru_stime for usage in
        (resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)))


def evaluate(database: Path, intent=None) -> dict:
    import resource

    before = database.read_bytes()
    intent = intent if intent is not None else IntentService(Registry(database)).prioritize("web", 600, ("background",))
    started, cpu_started = time.monotonic(), cpu_time()
    payload = evaluate_intent(intent, database)
    if database.read_bytes() != before:
        raise RuntimeError("Dry-run evaluation changed the registry")
    return {"wall_seconds": time.monotonic() - started, "self_and_child_cpu_seconds": cpu_time() - cpu_started,
        "process_lifetime_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "payload": payload}


def run(args) -> Path:
    cpu = preflight(args.cpu)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "status": "running", "mode": "application_comparison" if args.apply else "observation_only",
        "parameters": {**vars(args), "output": str(output)}, "environment": environment(cpu),
        "phases": [], "cleanup_errors": [], "error": None}
    phases = (*PHASES, "contended_applied") if args.apply else PHASES
    runtime = math.ceil(args.repetitions * len(phases) * (args.seconds + args.warmup + 20) + 120)
    services = Services(cpu, runtime)
    telemetry = LinuxTelemetry()
    try:
        with tempfile.TemporaryDirectory(prefix="senos-benchmark-") as temporary:
            state = Path(temporary)
            database = state / "registry.sqlite3"
            registry = Registry(database)
            ready = state / "web.json"
            web_unit = services.start("web", ready, args.web_iterations)
            port = wait_for_state(ready, web_unit)["port"]
            web, _ = registry.register("web", web_unit)
            if not request(port, args.timeout):
                raise RuntimeError("Interactive workload failed readiness request")
            rng = random.Random(args.seed)
            for trial in range(1, args.repetitions + 1):
                order = list(phases)
                rng.shuffle(order)
                for phase in order:
                    print(f"Trial {trial}/{args.repetitions}: {phase}", flush=True)
                    background = None
                    background_unit = None
                    background_registered = False
                    application = None
                    execution_session = None
                    action = None
                    phase_result = None
                    counter = state / f"background-{trial}-{phase}.json"
                    try:
                        if phase != "interactive_only":
                            background_unit = services.start("background", counter, args.background_iterations)
                            wait_for_state(counter, background_unit)
                            background, _ = registry.register("background", background_unit)
                            background_registered = True
                        # Warm up with the same client load, except in the background-only reference.
                        if phase == "background_only":
                            time.sleep(args.warmup)
                        else:
                            measure_requests(port, args.warmup, args.rate, args.timeout)
                        workloads = [web] + ([background] if background else [])
                        weights_before = [asdict(telemetry.read_cpu_weight(workload)) for workload in workloads]
                        evidence = [observation.model_dump(mode="json") for observation in telemetry.observe_all(workloads)]
                        if phase == "contended_applied":
                            from senos.executor.application import CPUApplication
                            intent = IntentService(registry).prioritize("web", max(60, math.ceil(args.seconds) + 30), ("background",))
                            application = CPUApplication(database)
                            execution_session = application.begin(intent)
                            evaluation = evaluate(database, intent)
                            action = application.apply(execution_session, evaluation["payload"])
                            print(f"CPU action: {action.status.value if action else 'no_action'}", flush=True)
                        else:
                            evaluation = evaluate(database) if phase == "contended_dry_run" else None
                        if evaluation:
                            decision = evaluation["payload"]["decision"]
                            label = "EVALUATION" if phase == "contended_applied" else "DRY RUN"
                            print(f"{label}: {decision['status']}: {decision['reason']}", flush=True)
                        host_before = host_snapshot()
                        initial = progress(counter) if background else None
                        measured_at = time.monotonic()
                        if phase == "background_only":
                            time.sleep(args.seconds)
                            samples, elapsed, missed = [], time.monotonic() - measured_at, 0
                        else:
                            samples, elapsed, missed = measure_requests(port, args.seconds, args.rate, args.timeout)
                        final = progress(counter) if background else None
                        host_after = host_snapshot()
                        evidence_after = [observation.model_dump(mode="json") for observation in telemetry.observe_all(workloads)]
                        weights_after = [asdict(telemetry.read_cpu_weight(workload)) for workload in workloads]
                        expected_weights = [dict(weight) for weight in weights_before]
                        if action is not None and action.status.value == "applied":
                            expected_weights[0]["weight"] = action.effective_cpu_weight
                        if expected_weights != weights_after:
                            raise RuntimeError("Workload weight or invocation changed during trial; results are invalid")
                        phase_result = {"trial": trial, "phase": phase,
                            "requests": summarize_requests(samples, elapsed, missed), "raw_requests": samples,
                            "background_batches_per_second": throughput(initial, final) if background else None,
                            "background_progress": {"before": initial, "after": final},
                            "telemetry_before": evidence, "telemetry_after": evidence_after,
                            "host_before": host_before, "host_after": host_after,
                            "weights_before": weights_before, "weights_after": weights_after,
                            "evaluation": evaluation, "execution": action.model_dump(mode="json") if action else None, "recovery": [], "execution_session": execution_session, "workloads": [workload.model_dump(mode="json") for workload in workloads]}
                        if phase != "background_only" and not phase_result["requests"]["successes"]:
                            raise RuntimeError("No successful HTTP requests; cannot report meaningful latency")
                        result["phases"].append(phase_result)
                    finally:
                        try:
                            if execution_session is not None:
                                restored = application.finish(execution_session)
                                if phase_result is not None:
                                    phase_result["recovery"] = restored
                                actual = [asdict(telemetry.read_cpu_weight(workload)) for workload in workloads]
                                if actual != weights_before:
                                    raise RuntimeError("Benchmark failed to restore original effective weights")
                        finally:
                            if background_unit:
                                services.stop(background_unit)
                                if background_registered:
                                    registry.unregister("background")
            result["status"] = "completed"
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        raise
    except Exception as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["launched_service_units"] = services.launched
        result["cleanup_errors"] = services.cleanup()
        if result["cleanup_errors"]:
            result["status"] = "cleanup_failed"
        (output / "result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        (output / "summary.md").write_text(markdown(result))
        print(f"Results: {output / 'summary.md'}", flush=True)
    if result["cleanup_errors"]:
        raise RuntimeError("Benchmark service cleanup failed; inspect result.json")
    return output


def bounded_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 60:
        raise argparse.ArgumentTypeError("Use a finite number greater than zero and at most 60")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 10000000:
        raise argparse.ArgumentTypeError("Use an integer from 1 through 10000000")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--apply", action="store_true", help="Add an explicitly applied CPU-policy phase on owned synthetic workloads, then restore.")
    result.add_argument("--output", type=Path, default=Path("benchmarks/results") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    result.add_argument("--seconds", type=bounded_float, default=10.0)
    result.add_argument("--warmup", type=bounded_float, default=10.0)
    result.add_argument("--rate", type=bounded_float, default=20.0, help="Offered requests/second from one client")
    result.add_argument("--timeout", type=bounded_float, default=2.0)
    result.add_argument("--repetitions", type=int, choices=range(1, 11), default=3)
    result.add_argument("--cpu", type=int, help="Allowed logical CPU for both synthetic workloads")
    result.add_argument("--web-iterations", type=positive_int, default=20000)
    result.add_argument("--background-iterations", type=positive_int, default=20000)
    result.add_argument("--seed", type=int, default=42)
    return result


def main():
    try:
        run(parser().parse_args())
    except KeyboardInterrupt:
        print("Benchmark interrupted; inspect the result file for cleanup status.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
