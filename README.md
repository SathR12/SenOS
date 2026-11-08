# SenOS

**Natural-language user intent driving Linux resource policies.**

SenOS is a proposed lightweight, multi-agent orchestration layer that translates user goals into bounded Linux resource-management decisions. It aims to improve application responsiveness while preserving system stability and acceptable progress for background workloads.

For example:

> "Prioritize this interactive application for the next ten minutes while keeping my background computation running."

SenOS will interpret the request, identify registered workloads, observe resource contention, propose policy changes, validate them, and measure their effects.

SenOS runs in Linux user space and uses existing operating-system controls. It does not require building a new kernel or scheduler.

## Project status

This repository currently contains project documentation, a Python package skeleton, and folders for tests, benchmarks, deployment, and development scripts. Dependencies are managed with uv, and the `senos` CLI supports `version`, persistent `register`, and removal through `unregister`. `status` reads live CPU, memory, and pressure for registered Linux units and offers explicitly labeled demo readings with `--demo`; `restore` recovers recorded CPU weights; see [restore usage](docs/restore.md). The shared contracts and registry adapter are implemented; see [contract semantics](docs/contracts.md). Live read-only systemd/cgroup v2 telemetry is implemented. Agent behavior and automatic policy application are not implemented. Restore includes a restricted CPU-weight backend and persistent original-settings journal.

**We are building the CLI first.** A UI can follow once the controller, safety checks, and experiments work. Both interfaces should use the same application services and validated data contracts.

## First milestone

Demonstrate one scenario inside an isolated Linux development VM:

1. Launch an interactive test application and a background CPU workload.
2. Register both workloads and measure baseline latency and throughput.
3. Accept a user objective through the CLI.
4. Adjust relative CPU allocation within configured bounds.
5. Verify that responsiveness improves while background work continues.
6. Restore the previous settings when the objective expires or the user requests restoration.

Start with deterministic CPU policies. Add natural-language interpretation after the control mechanism works. Collect memory telemetry early, then add memory-policy changes in a later milestone.

Initial exclusions: arbitrary process control, process termination, kernel modifications, real-time scheduling, CPU affinity tuning, global kernel tuning, malware detection, and distributed orchestration.

## Proposed stack

| Area | Starting choice | Purpose |
| --- | --- | --- |
| Reference environment | Ubuntu 24.04 LTS VM | Consistent Linux environment for development and experiments |
| Language | Python 3.12 | Controller, agents, telemetry, and CLI |
| Dependencies | uv and `pyproject.toml` | Reproducible dependency management |
| CLI | Typer | Submit objectives, inspect state, and restore policies |
| Data contracts | Pydantic | Validate intents, workloads, observations, proposals, and actions |
| Telemetry | psutil, `/proc`, cgroup statistics, PSI | Observe workload activity and resource contention |
| Policy backend | systemd over cgroup v2 | Manage workload groups and resource settings |
| Persistence | SQLite and structured logs | Record experiments, policy history, and recovery state |
| Testing | pytest and Linux VM integration tests | Verify decisions, safety checks, and actual policy effects |
| Language model | Replaceable provider adapter | Convert natural language into a constrained intent |
| Containers | Docker and Compose where useful | Reproducible benchmark workloads and optional supporting services |
| UI | Deferred | Reuse the controller through a service/API boundary when needed |

These are initial engineering choices, not installed dependencies or guaranteed minimum requirements. A web framework, frontend framework, message broker, and multi-agent framework are not required for the first milestone.

## Linux and Docker requirements

Use a dedicated Linux VM as the reference development environment. A suggested starting allocation is **4 vCPUs, 8 GB RAM, and 30 GB disk**, adjusted to the experiments being run.

Before enabling policy changes, verify:

- systemd is running.
- cgroup v2 and the CPU and memory controllers are available.
- Pressure Stall Information (PSI) is available.
- The executor has explicitly scoped permission to manage SenOS workloads.
- Workload launch, policy restoration, and recovery work inside the VM.

**Docker supports the project; it is not the host-control boundary.** Initially, run the controller and restricted executor directly in the VM, and launch native benchmark workloads through systemd. Use containers when they improve workload reproducibility. A later Docker adapter should manage Docker-owned workloads through the appropriate Docker interfaces.

Docker Desktop on Windows runs Linux containers in a Linux environment. SenOS running there would manage eligible Linux workloads, not Windows applications. WSL can be useful for development, but integration results should come from the agreed Linux VM configuration.

Do not use a privileged container as the default installation or give the language model direct access to the Docker socket or shell.

## Agent responsibilities

The five agents initially live as modules within one controller service. Separate responsibilities do not require separate deployments or a language-model call for every decision.

| Agent | Responsibility | Initial output |
| --- | --- | --- |
| Orchestrator | Interpret goals, coordinate proposals, and resolve conflicts | Selected policy plan |
| CPU optimization | Evaluate CPU contention and application latency | Bounded CPU-weight proposal |
| Memory optimization | Observe memory use and pressure | Warnings; memory-policy proposals later |
| Process management | Track registered workloads, process groups, and lifecycle | Workload registry |
| Security and reliability | Check scope, bounds, cooldowns, expiration, and recovery conditions | Accept/reject decision with a reason |

The process agent starts with explicit workload registration. Automatic inference of arbitrary application dependencies is deferred. The security and reliability role starts with policy validation and recovery, rather than general suspicious-process detection.

## Architecture and safety

```text
CLI (UI later)
      |
Intent parsing and validation
      |
Orchestrator <--- telemetry and workload registry
      |
Agent proposals
      |
Deterministic safety checks
      |
Restricted executor
      |
systemd / cgroup v2 ---> managed workloads
      |
Measurements, action history, and recovery
```

The language model translates requests into structured objectives. Deterministic code authorizes and applies changes. Unknown or ambiguous workload names must be resolved before execution.

Safety requirements for the first implementation:

- Only explicitly registered or SenOS-launched workloads are eligible.
- The executor accepts allowlisted operations and validated workload identifiers, never arbitrary shell commands or paths.
- Changes have configured bounds, cooldowns, and expiration times.
- Record previous settings before applying a change.
- Provide dry-run and restore operations.
- Persist enough state for recovery after a controller failure; do not rely only on normal shutdown cleanup.
- Record observations, proposals, validation results, applied changes, and measured outcomes.

Start with systemd `CPUWeight`, which adjusts relative CPU allocation under contention rather than guaranteeing a percentage. Later, evaluate `MemoryHigh` carefully: it can trigger reclaim and throttling. Adaptive hard memory limits are outside the initial scope because they can lead to out-of-memory behavior.

## Repository structure

The folders below are scaffolded. Registration includes service, systemd-resolution, configuration, and storage modules; the remaining components are scaffolded; empty non-package folders use `.gitkeep` so Git preserves them.

```text
SenOS/
|-- README.md
|-- LICENSE
|-- pyproject.toml                 # Dependencies and tool configuration
|-- uv.lock                        # Generated dependency lockfile
|-- src/
|   `-- senos/
|       |-- cli.py                 # CLI entry point
|       |-- config.py              # Validated application configuration
|       |-- contracts/             # Intent, Workload, Observation, Proposal, ActionResult
|       |-- intent/                # Natural-language parsing and provider adapters
|       |-- agents/                # Orchestrator, CPU, memory, process, reliability
|       |-- telemetry/             # proc, cgroup, PSI, and application measurements
|       |-- workloads/             # Registration, identity, and lifecycle tracking
|       |-- policy/                # Decision rules, conflict resolution, safety checks
|       |-- executor/              # Restricted operations and systemd backend
|       |-- storage/               # SQLite persistence and action history
|       `-- recovery/              # Expiration, restoration, and restart reconciliation
|-- tests/
|   |-- unit/                      # Pure logic and contract validation
|   |-- integration/               # Linux/systemd policy and recovery tests
|   `-- fixtures/                  # Recorded observations and test inputs
|-- benchmarks/
|   |-- workloads/                 # Interactive, CPU, and later memory workloads
|   |-- scenarios/                 # Repeatable contention experiments
|   `-- analysis/                  # Compare latency, throughput, and overhead
|-- deploy/
|   |-- systemd/                   # Controller/executor units and permission setup
|   `-- docker/                    # Optional workload images and Compose files
|-- scripts/                       # Environment preflight and development helpers
`-- docs/
    |-- architecture.md            # Component boundaries and design decisions
    |-- development.md             # Reproducible Linux setup
    |-- safety.md                  # Permissions, allowed actions, and recovery
    `-- experiments.md             # Benchmark protocol and results
```

Keep CLI presentation separate from policy logic so a future UI can reuse the same services. Add an API or frontend directory when that milestone begins.

## Development roadmap

| Milestone | Deliverable | Completion check |
| --- | --- | --- |
| 1. Environment and baseline | VM instructions, preflight, two workloads | Teammates can reproduce the same experiment |
| 2. Observe-only CLI | Registry, telemetry, logs, status command | Attribute load and application latency correctly |
| 3. CPU control | Deterministic policy, executor, expiration, restoration | Improve responsiveness while preserving background progress |
| 4. Natural-language intent | Validated intent parser and explanations | Different phrasings produce appropriate bounded objectives |
| 5. Memory and evaluation | Pressure experiments and bounded memory policies | Document reproducible benefits and failure cases |
| 6. UI | Interface over existing application services | Inspect workloads, submit intent, and review/restore changes |

### Suggested team ownership

- **Platform:** Linux setup, systemd integration, restricted executor.
- **Telemetry:** workload registry, CPU/memory observations, application metrics.
- **Policy and intent:** orchestrator, optimization proposals, natural-language parser.
- **Reliability and evaluation:** safety validation, recovery, benchmark harness.

Agree on shared contracts and their example payloads before implementing modules independently. All privileged operations should pass through the executor boundary.

### First contributor tasks

1. Agree on the reference VM image and record its CPU, RAM, swap, kernel, and systemd configuration.
2. Add `pyproject.toml`, generate the dependency lockfile, and implement the CLI entry point in the existing package skeleton.
3. Define shared contracts and workload registration behavior.
4. Build the interactive and background workloads and record an unmanaged baseline.
5. Implement observe-only telemetry before enabling writes.
6. Add one bounded CPU-weight operation, then verify restoration, rejected actions, and crash recovery.

To set up a development environment, install [uv](https://docs.astral.sh/uv/) and run `uv sync`. Run the tests with `uv run pytest` and the CLI with `uv run senos --help`. `senos version`, `senos register NAME --unit UNIT`, `senos unregister NAME`, and `senos unregister --all` work. See [workload registration](docs/registration.md) for a WSL/Linux example, persistence, validation, and integration testing. `senos status` reads live metrics on supported Linux hosts and otherwise lists registrations with unavailable metrics; `senos status --demo` shows labeled synthetic samples. See [status usage](docs/status.md). `senos restore [NAME]` or `senos restore --all` restores recorded CPU settings. Without recorded changes it reports a successful no-op; see [restore usage](docs/restore.md).

## Collaboration

Follow [AGENTS.md](AGENTS.md) for coding-agent contributions. Start focused task branches from current `main`, using `feat/`, `fix/`, `docs/`, `test/`, `chore/`, or `refactor/` plus a lowercase, hyphen-separated task name. Keep CLI presentation separate from services and coordinate shared-contract changes with their owner.

Publish changes through a pull request into `main`, explain the behavior and validation, request at least one teammate review, then squash-merge and delete the task branch. Do not push directly to `main` or force-push shared branches. A maintainer must configure GitHub branch protection separately; this documentation does not enforce it.

## Evaluation

Compare Linux defaults, a manually configured static policy, and the SenOS adaptive policy under the same workload and VM configuration.

Measure:

- Interactive application p95/p99 latency.
- Background throughput and completion time.
- CPU and memory pressure.
- Controller CPU and memory overhead.
- Policy-change frequency, rejected actions, and restoration/recovery behavior.

Choose an acceptable background slowdown before tuning and repeat experiments. Higher overall CPU utilization alone is not evidence of a better user experience. Memory throttling and some scheduling changes can have effects that are not undone simply by restoring a setting, so experiments must measure outcomes as well as configuration.

## Technical references

- [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Linux Pressure Stall Information](https://docs.kernel.org/accounting/psi.html)
- [systemd resource controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [Docker Desktop WSL backend](https://docs.docker.com/desktop/features/wsl/)

## License

See [LICENSE](LICENSE).
