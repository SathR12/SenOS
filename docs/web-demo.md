# Visible, repeatable web demo

From WSL/Linux, run this single command from the repository root:

```bash
make demo
```

Open the exact `OPEN http://localhost:PORT` URL printed in the terminal. Port 8765
is preferred; if another application occupies it, the launcher binds a free port
before creating workloads. Only one SenOS web demo runs at a time, to prevent two
experiments from contaminating each other's CPU measurements. Stop a previous demo
with Ctrl+C before starting another. Do not type the URL into a search engine.

The launcher uses the project's existing `.venv/bin/python`; if absent, it uses
`uv run`. On a fresh checkout, install uv and run `uv sync` first. It resolves
paths relative to the repository, so it can also be called from another directory:

```bash
bash /mnt/j/Code/Projects/SenOS/scripts/web-demo.sh
```

## What happens automatically

1. Validate Linux, cgroup v2, systemd user access, and local AI configuration.
2. Start two uniquely named synthetic services at CPUWeight=100 on one allowed CPU.
3. Warm up pressure for 10 seconds and measure baseline HTTP latency for 15 seconds.
4. Ask the configured model to evaluate the objective entered in the dashboard
   (pre-filled with "Keep local-app responsive for one minute while
   background-build keeps progressing"). You can replace it during warmup; model resolution
   takes additional time, shown separately.
5. Monitor the validated objective locally. If pressure and safety checks pass,
   apply the bounded web boost from 100 to 200; background weight remains 100.
6. Verify automatic restoration, collect 10 seconds of restored measurements,
   stop the owned services, and preserve the results page for five minutes.

The one-minute deadline starts when the Intent is created. The verified boost may
last less than a minute because validation and sampling consume some of that time.
The page itself runs separately from the contended workload so the dashboard stays
responsive. Every latency value comes from a real HTTP request; no artificial
sleep is inserted into the workload.

The dashboard leads with the familiar scenario of using a local app while a
CPU-heavy background build continues. It shows a five-step timeline, countdowns,
plain-language before/during results, background progress, and restoration checks.
Use **Load app** to send your own real request to the contended app
before and during the temporary boost. Personal preview clicks are displayed
separately and are not recorded as formal samples. They still add real work, so
repeated clicks can influence the run.

Technical details remain available in expandable sections: actual CPU weights,
latency history, controller decisions, and a table with each phase's mean, p95,
successful/failed request counts, background batches/second, and measured counter
span. Baseline and phase summaries survive chart scrolling. Transition requests
and model-resolution requests are excluded from baseline/prioritized comparisons.
A brief progress-file replacement gap on WSL is retried for at most one second;
a persistent missing file is a visible failure, not a fabricated measurement.

Both live settings and durable recovery records are checked before marking a run
restored. Failed requests and controller failures remain visible. A rejected repeat
boost is a safety decision; the current 200 weight may remain in effect. No-action
runs are labeled `no_action`, with no claimed improvement. Longer latency is shown
as a regression, not hidden. Percentages vary with host activity; this is a synthetic
visual demonstration, not a statistical performance guarantee or a Zoom benchmark.

## Offline rehearsal and repeat checks

To exercise the same executor and recovery without a model/API dependency:

```bash
bash scripts/web-demo.sh --mode deterministic
```

This mode is explicitly labeled DETERMINISTIC in the page and terminal. It submits
the same structured objective directly; it does not demonstrate natural-language
interpretation. For serial runs that close the dashboard automatically:

```bash
bash scripts/web-demo.sh --mode deterministic --exit-after-run
bash scripts/web-demo.sh --mode deterministic --exit-after-run
```

`--port 0` requests a free port. `--auto-ai` remains an alias for AI mode; AI mode
is now the default and starts automatically. There is no second terminal command,
manual registration step, or copied temporary database path.

## Enter a custom intent

In AI mode, edit **Your goal** in the dashboard and select **Save goal** during
warmup. The text is sent only to the local demo server,
then passed as the natural-language request to `senos agent run`. The form
accepts up to 500 characters and becomes locked when baseline measurement ends.
Deterministic/offline mode intentionally keeps its fixed rehearsal objective.

## Stop and inspect

Press Ctrl+C in the launcher terminal. It interrupts the controller, verifies
recovery, and stops its owned services. You can also create a file named `STOP`
inside the exact run directory printed by the terminal. An independent recovery
service handles controller crashes; see [CPU execution limits](cpu-execution.md).

Each run prints its private, ignored `var/web-demo/run-...` folder containing:

- `summary.json`: final phase statistics, errors, application and restoration status.
- `samples.jsonl`: individual HTTP measurements, actual weights, and progress counts.
- `agent.log`: structured CLI events or provider failure details.
- `units.json`: only the services owned by this run.
- `configuration.json`: kernel, Python, affinity, workload sizes, and timing parameters.
- Local registry and monitoring SQLite databases.

Do not commit these artifacts or `.env`. The browser only receives read-only
metrics through `/metrics`; no API keys or environment contents are sent to it.
If AI configuration or provider access fails, fix the displayed error or rehearse
with `--mode deterministic`; there is no silent provider fallback.

## Verification

```bash
uv run pytest tests/unit/test_web_demo.py tests/unit/test_benchmark.py
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest tests/integration/test_web_demo_systemd.py
```

The checks cover executable resolution, path independence, occupied ports, stable
phase summaries, missing-file retry limits, no-action/failure reporting, HTTP
endpoints, and cleanup after startup failure. The opt-in integration test performs
two serial offline runs from a different
working directory, checks phase results and recovery, and verifies that all owned
services stopped. It takes several minutes. Unit checks alone cannot prove Linux
application or recovery behavior.
For controlled repeated latency/throughput experiments, use the [benchmark](experiments.md).

For shorter everyday commands and independent Linux process verification, see [command shortcuts](commands.md).
