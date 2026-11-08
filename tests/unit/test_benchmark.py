import json
import subprocess

import pytest

from benchmarks.analysis.report import comparisons, markdown, percentile, summarize_requests
from benchmarks.scenarios import cpu_contention as scenario


def test_latency_summary_keeps_errors_and_missed_arrivals_visible():
    samples = [{"ok": True, "latency_ms": value} for value in range(1, 101)]
    samples.append({"ok": False, "latency_ms": 2000})
    result = summarize_requests(samples, 10, 7)
    assert result["p95_ms"] == 95 and result["p99_ms"] == 99
    assert result["errors"] == 1 and result["missed_arrivals"] == 7
    assert result["successful_requests_per_second"] == 10
    assert percentile([], 95) is None
    assert summarize_requests([], 1, 0)["p95_ms"] is None


def test_comparisons_pair_trials_and_do_not_claim_policy_improvement():
    phases = []
    for trial, ratio in [(1, 2), (2, 4)]:
        for phase, latency, progress in [("interactive_only", 10, None), ("background_only", None, 100), ("contended_dry_run", 10 * ratio, 50)]:
            phases.append({"trial": trial, "phase": phase, "requests": {"p95_ms": latency}, "background_batches_per_second": progress})
    assert comparisons(phases) == {"latency_contention_ratio": 3, "background_progress_ratio": 0.5}
    text = markdown({"status": "failed", "phases": []})
    assert "no proposal was applied" in text
    assert "N/A" in text


@pytest.mark.parametrize("option,value", [("--seconds", "0"), ("--warmup", "nan"), ("--rate", "inf"), ("--timeout", "61"), ("--repetitions", "0"), ("--web-iterations", "-1")])
def test_invalid_benchmark_parameters_are_rejected(option, value):
    with pytest.raises(SystemExit):
        scenario.parser().parse_args([option, value])


def test_throughput_uses_counter_timestamps_and_rejects_resets():
    assert scenario.throughput({"completed_batches": 10, "monotonic": 1}, {"completed_batches": 20, "monotonic": 3}) == 5
    with pytest.raises(ValueError):
        scenario.throughput({"completed_batches": 10, "monotonic": 1}, {"completed_batches": 5, "monotonic": 3})
    with pytest.raises(ValueError):
        scenario.throughput({"completed_batches": 10, "monotonic": 1}, {"completed_batches": 10, "monotonic": 1})


def test_service_launch_is_bounded_and_cleanup_cannot_stop_unowned_units(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(scenario, "command", lambda arguments, **kwargs: calls.append(arguments))
    services = scenario.Services(2, 120)
    unit = services.start("web", tmp_path / "web.json", 100)
    arguments = calls[0]
    assert "--property=RuntimeMaxSec=120" in arguments
    assert "--property=CPUWeight=100" in arguments
    assert "-c" in arguments and "2" in arguments
    with pytest.raises(ValueError, match="not owned"):
        services.stop("dbus.service")
    assert services.cleanup() == []
    assert calls[-1] == ["systemctl", "--user", "stop", "--", unit]
    assert not services.owned


def test_cleanup_failure_keeps_unit_known_for_diagnostics(monkeypatch):
    services = scenario.Services(0, 120)
    services.owned.add("senos-bench-test.service")
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("systemctl", 10)
    monkeypatch.setattr(scenario, "command", fail)
    assert services.cleanup()
    assert services.owned == {"senos-bench-test.service"}


def test_failure_writes_partial_result_and_cleans_owned_services(monkeypatch, tmp_path):
    monkeypatch.setattr(scenario, "preflight", lambda cpu: 0)
    monkeypatch.setattr(scenario, "environment", lambda cpu: {})
    cleaned = []
    monkeypatch.setattr(scenario.Services, "cleanup", lambda service: cleaned.append(True) or [])
    def fail(*args):
        raise RuntimeError("intentional startup failure")
    monkeypatch.setattr(scenario.Services, "start", fail)
    output = tmp_path / "result"
    args = scenario.parser().parse_args(["--output", str(output)])
    with pytest.raises(RuntimeError, match="startup failure"):
        scenario.run(args)
    payload = json.loads((output / "result.json").read_text())
    assert payload["status"] == "failed"
    assert "startup failure" in payload["error"]
    assert (output / "summary.md").is_file() and cleaned == [True]
    with pytest.raises(FileExistsError):
        scenario.run(args)


def test_request_sampling_reports_missed_arrivals_and_errors(monkeypatch):
    class Clock:
        value = 0.0
        def now(self):
            return self.value
        def sleep(self, seconds):
            self.value += seconds
    clock = Clock()
    monkeypatch.setattr(scenario.time, "monotonic", clock.now)
    monkeypatch.setattr(scenario.time, "sleep", clock.sleep)
    def slow(*args):
        clock.sleep(0.25)
        raise TimeoutError("slow response")
    monkeypatch.setattr(scenario, "request", slow)
    samples, elapsed, missed = scenario.measure_requests(1234, 1, 20, 1)
    assert missed > 0
    assert elapsed >= 1 and samples
    assert all(not sample["ok"] and sample["error"] == "TimeoutError" for sample in samples)


def test_request_arrivals_do_not_accumulate_floating_point_deadline_errors(monkeypatch):
    class Clock:
        value = 10000.0
        def now(self):
            return self.value
        def sleep(self, seconds):
            self.value += seconds
    clock = Clock()
    monkeypatch.setattr(scenario.time, "monotonic", clock.now)
    monkeypatch.setattr(scenario.time, "sleep", clock.sleep)
    def fast(*args):
        clock.sleep(0.005)
        return True
    monkeypatch.setattr(scenario, "request", fast)
    samples, elapsed, missed = scenario.measure_requests(1234, 2, 20, 1)
    assert len(samples) == 40 and missed == 0
    assert elapsed == pytest.approx(2)
