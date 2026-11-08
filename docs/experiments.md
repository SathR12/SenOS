# CPU contention benchmark

The benchmark measures a synthetic interactive HTTP service and a background CPU
workload in three conditions. It connects the existing structured intent and CPU
dry-run pipeline to a reproducible experiment. By default it does not apply proposals. `--apply` adds an explicitly applied comparison on owned synthetic workloads and verifies restoration. Neither mode calls
an LLM or changes existing applications; results are experimental rather than a general performance guarantee.

## Run in WSL or Linux

Run from the repository root in an isolated Linux development environment with a
working systemd user session, cgroup v2, Python 3.12+, and `taskset` (util-linux).
No root privileges, Docker, frontend, or API keys are required. On WSL, results
reflect the Linux guest and its host contention, not Windows applications.

```bash
uv sync
systemctl --user show-environment
uv run python -m benchmarks.scenarios.cpu_contention --help

# Smoke test: verifies setup and results, not statistical significance.
uv run python -m benchmarks.scenarios.cpu_contention --seconds 2 --warmup 1 --repetitions 1

# Longer repeated experiment for comparison.
uv run python -m benchmarks.scenarios.cpu_contention --seconds 30 --warmup 10 --repetitions 3
```

The default run uses three repetitions, ten measured seconds per condition, and
ten warmup seconds per condition. It offers 20 requests/second. Longer runs are
better for tail latency; a p99 from a handful of requests is weak evidence.

The runner creates a unique result folder under ignored `benchmarks/results/`
and prints its path. Use `--output PATH` to select a new directory; an existing
path is rejected so previous results cannot be overwritten. Keep results in the
ignored directory or outside Git.

## What it launches and measures

Both synthetic workloads run as unique systemd user services pinned to the same
allowed logical CPU. This deliberately constructs contention on multicore hosts.
Pinning and equal initial `CPUWeight=100` values are fixed experiment setup, not
an adaptive SenOS action. The launcher only starts these repository workloads.
An operating-system runtime limit bounds the temporary services' lifetime.

Each trial randomizes these three conditions using the recorded `--seed`:

| Condition | Workload | Measurements |
| --- | --- | --- |
| `interactive_only` | HTTP service receiving requests | Reference response latency and request rate |
| `background_only` | Background hash computation; HTTP service idle | Reference completed batches/second |
| `contended_dry_run` | Both workloads | Response latency, background progress, live evidence, intent and CPU proposal |

The HTTP handler performs fixed-count SHA-256 work per request. The background
worker repeats fixed-size batches and publishes counters with monotonic timestamps.
This is a CPU-focused synthetic application, not a real video call or compiler.

After warmup, the contended phase submits a structured intent prioritizing `web`
and preserving `background`. The exact intent, observations, warnings, proposal or
no-action reason, evaluation wall time, and process/child CPU time are saved.
Evaluation happens **before** the measured HTTP window, so its overhead is reported
separately rather than mixed into response times. Peak RSS is the runner's lifetime
high-water mark on Linux, not memory attributable only to the evaluation call.
There is no continuous controller whose overhead is measured by this experiment.

The single HTTP client offers a fixed arrival rate with no catch-up bursts. It
records every attempted request, failure, and arrival missed while a previous
request was in flight. Latency percentiles cover successful requests only; always
inspect errors and missed arrivals alongside them. This client is not a full
open-loop load generator; increase or decrease `--rate` deliberately and compare
only runs with matching parameters. Counters use their own publication timestamps
for background throughput (approximately 100 ms counter granularity).

Read-only unit telemetry before/after the measurement window includes CPU usage,
memory use, and PSI where available. Host snapshots record CPU counters, pressure,
and load averages. Environment metadata includes kernel, Python, systemd, memory,
swap, allowed CPUs, and WSL detection. Unavailable values remain null/N/A.

## Inspect results

Each result folder contains:

- `summary.md`: per-trial p95/p99 latency, request failures/missed arrivals, and background throughput.
- `result.json`: raw requests, parameters, environment, live telemetry, intent/proposal, evaluation overhead, service identities, cleanup status, and errors.

```bash
# Replace the timestamp with the folder printed by your run.
cat benchmarks/results/TIMESTAMP/summary.md
uv run python -m benchmarks.analysis.report benchmarks/results/TIMESTAMP/result.json
```

The summary reports median paired-trial ratios:

- `latency_contention_ratio`: contended p95 divided by interactive-only p95; above one means worse latency under contention.
- `background_progress_ratio`: contended background batches/second divided by background-only batches/second; below one means background slowdown.

These compare contention with isolated workloads. They are **not** improvements
from SenOS: weights remain unchanged. A successful `no_action` decision can mean
low pressure, missing controller access, noncomparable cgroups, or insufficient
evidence. Inspect the stored reason instead of expecting every run to propose.

Pre/post effective weights and invocation identities must match or the run fails.
Intent evaluation must leave registry bytes unchanged. The benchmark registry and
working files live in a private temporary directory, separate from your saved
registrations. Only this run's services are stopped, including on normal Python
exceptions and Ctrl+C. Cleanup failures are saved and return a failing exit code.
If the runner is forcibly killed or the machine stops, service `RuntimeMaxSec`
provides eventual workload termination; this is not policy restart recovery.

## Interpreting evidence and next milestones

Record configuration and offered load, repeat runs, keep raw observations, and
compare equivalent conditions. The client and host can also compete for CPU;
randomized phase order reduces order bias but does not remove host noise. Synthetic
single-core pressure is a controlled experiment, not evidence for all desktop apps.

To establish policy benefit later, add separately validated application with
original-setting capture, verification, expiration, and restart recovery. Compare
Linux defaults, an explicitly chosen static policy, and the controller under the
same conditions. Predeclare acceptable background slowdown and measure restoration.
The current benchmark implements no execution path and makes no policy changes.

A future LLM can consume the existing intent interface; these measurements provide
a baseline and recorded examples for reasoning about when intervention is useful.

## Source and verification

- [Workloads](../benchmarks/workloads/cpu.py): synthetic HTTP handler and background counter.
- [Scenario](../benchmarks/scenarios/cpu_contention.py): preflight, isolated launch, sampling, dry-run evaluation, and cleanup.
- [Analysis](../benchmarks/analysis/report.py): nearest-rank percentiles and paired comparisons.
- [Unit tests](../tests/unit/test_benchmark.py): statistics, request scheduling, validation, cleanup, and failure artifacts.
- [Linux smoke test](../tests/integration/test_benchmark_systemd.py): real services, measurements, unchanged settings, and cleanup.

```bash
uv run pytest tests/unit/test_benchmark.py
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest tests/integration/test_benchmark_systemd.py
```

## Add a verified applied-policy comparison

```bash
uv run python -m benchmarks.scenarios.cpu_contention --apply --seconds 10 --warmup 10 --repetitions 3
```

This adds a randomized `contended_applied` phase. The existing policy decides from
fresh telemetry; a no-action decision is not overridden to force a result. A
verified application changes the target weight within its original-relative cap,
measures the HTTP and background workloads, then restores originals before
stopping the temporary services. Actions and restoration outcomes are saved.

`applied_vs_contended_latency_ratio` compares applied p95 with equal-weight
contention; less than one means lower measured tail latency. The corresponding
background ratio quantifies the throughput cost. Ratios exclude phases without a
verified applied action. Inspect sample counts, errors, missed arrivals, and all
trials rather than selecting only favorable results. See [CPU execution](cpu-execution.md)
for recovery behavior and restrictions. An applied phase is not a full continuously
adapting policy benchmark; it measures one bounded policy while workloads run.
