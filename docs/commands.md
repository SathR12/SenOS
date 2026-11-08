# Everyday commands

Run `make help` inside WSL/Linux from the repository root. Make uses the existing
project environment, so you do not need to type `uv run` for each operation.
Install `make` with your distribution's package manager if it is missing; it is
already available in the tested WSL environment. On a fresh checkout, run `uv sync`
once first. The original `senos` CLI and its safety checks remain unchanged.

## Present the web demo

Terminal 1:

```bash
make demo
```

Open the exact URL printed by the launcher. The AI demo starts automatically.
For an offline rehearsal use `make demo-offline`; this is labeled deterministic
and does not call a model. Both commands change only their own synthetic services.

Terminal 2, while the demo runs:

```bash
make demo-status
make proof
```

`demo-status` invokes the existing SenOS status command with the correct demo
registry. `proof` independently reads Linux information rather than dashboard data:

- systemd service state, real MainPID, cgroup path, configured CPUWeight and CPU time.
- The actual Python workload command line from `/proc/PID/cmdline`.
- The kernel's `cpu.weight`, two `cpu.stat` counters and their measured CPU rate.
- Raw `cpu.pressure` readings.

It prints the corresponding `systemctl` command so you can run it yourself.
Use `top -p PID` with a printed PID for another independent view. A half-second
CPU sample can be low when the web server is waiting between requests; that does
not undo its configured priority. The background workload continuously computes.

Expect web weight 100 before, 200 when an action is applied, and 100 after
restoration. Background weight stays 100. After cleanup, proof correctly reports
stopped services instead of pretending the saved run is still live.

After the experiment:

```bash
make results
make demo-stop
```

`results` prints mean and p95 latency, errors, background batches/second, and the
measured counter span. `demo-stop` requests graceful shutdown; Ctrl+C in terminal 1
also works. No temporary database path needs to be copied. Demo helpers select the
latest run; normal commands continue to use your normal workload registry.

## Understand background progress

The counter is cumulative, and the phases last different amounts of time. For
example, 100 batches in 10 seconds is **10 batches/s**, while 300 batches in 60
seconds is **5 batches/s**. The second total is larger despite slower progress.

The dashboard and terminal now calculate:

```text
background batches/second = change in completed batches / elapsed counter time
```

Counter timestamps are monotonic and published by the actual background process.
A phase needs at least two counter readings to produce a rate. Older saved runs
without rate fields display N/A; they are not silently reinterpreted.

This is a **synthetic application doing real work**, not a simulation of CPU
settings: the HTTP server and background process compute fixed SHA-256 workloads,
Linux schedules them, and the executor changes the real cgroup weight. The
requester measures real HTTP round-trip time. It illustrates CPU contention;
it does not establish benefits for Zoom, network congestion, or arbitrary apps.
Compare latency and normalized background throughput together, and repeat the
[controlled benchmark](experiments.md) before making broad performance claims.

## Normal workload commands

```bash
make status
make register NAME=web UNIT=my-web.service
make remove NAME=web
make ask TEXT="Prioritize web for one minute while keeping build progressing"
make apply TEXT="Prioritize web for one minute while keeping build progressing"
make prioritize NAME=web KEEP=build
make restore NAME=web
```

`ask` is dry-run AI advice. `apply` explicitly enables watched CPU application and
restoration. `prioritize` submits a direct, non-LLM objective with real CPU changes
for 60 seconds; use `DURATION=120` to change that duration. `KEEP` is optional and
accepts one workload name; the full CLI supports repeated preserve options.
`remove` only removes registration and never stops the application. `restore`
requires a name so an accidentally empty argument cannot restore every workload.
Registration resolves user-manager units; advanced system-manager registration
remains available through the full CLI.

Add `DB=/absolute/path/registry.sqlite3` to choose a custom registry for normal
commands. Every shortcut delegates to the existing CLI and restricted executor.
Text is passed as an argument, never executed as shell code.

For any existing CLI option, and for tests:

```bash
make cli ARGS="status --json"
make cli ARGS="agent cpu --demo"
make test
```

`make test` runs unit tests; opt-in Linux integration checks are separate. See the
[web demo guide](web-demo.md) for lifecycle and repeated-run validation.
