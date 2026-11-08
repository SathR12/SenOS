# Technology stack

SenOS is a Python application that runs in Linux user space and manages only
explicitly registered systemd workloads. The first milestone deliberately uses
a small stack: a CLI and application services, deterministic policy code,
SQLite persistence, and Linux's existing resource-control interfaces.

This page distinguishes technologies used by the repository today from planned
or optional choices. Version constraints are authoritative in
[`pyproject.toml`](../pyproject.toml), and exact resolved dependency versions are
recorded in [`uv.lock`](../uv.lock).

## Current application stack

| Area | Technology | How SenOS uses it |
| --- | --- | --- |
| Language | Python 3.12 or later | Implements the CLI, contracts, telemetry, policy services, executor, persistence, and recovery code. |
| CLI | Typer | Exposes registration, status, intent, monitoring, CPU-control, and restore commands. CLI presentation remains separate from reusable application services. |
| Data contracts | Pydantic 2 | Defines and validates workloads, observations, intents, proposals, snapshots, and action results passed between components. |
| Host metrics | psutil, `/proc`, cgroup v2 files, and PSI | Collects process, workload, memory, CPU, and system-pressure information. Missing measurements remain explicitly unavailable rather than being guessed. |
| Persistence | Python `sqlite3` and SQLite | Stores registrations, monitoring history, execution state, and recovery records locally. Runtime databases are not committed. |
| Resource control | systemd over cgroup v2 | Resolves registered `.service` and `.scope` units and applies bounded runtime `CPUWeight` changes through a restricted executor. |
| HTTP clients | Python standard library | Calls configured language-model APIs without adding a separate HTTP-client dependency. |
| Packaging | `pyproject.toml` and Hatchling | Defines package metadata, dependencies, the build backend, and the `senos` console entry point. |

The direct runtime dependencies are intentionally limited to `psutil`,
`pydantic`, and `typer`. Transitive packages may also be installed by the
dependency manager, but they are not application-level stack decisions.

## Reference platform

Ubuntu 24.04 LTS in a dedicated VM is the reference environment for policy and
benchmark testing. A compatible environment needs:

- Linux with systemd running;
- a unified cgroup v2 hierarchy with the required controllers delegated;
- Pressure Stall Information when pressure measurements are required; and
- permission for the restricted executor to inspect and manage only the
  registered SenOS units.

WSL is useful for development, but resource-control results should be verified
in the agreed Linux VM because controller delegation and systemd behavior can
differ. SenOS manages eligible Linux workloads; it does not manage Windows
applications from Docker Desktop or WSL.

## Development and verification

| Area | Technology | Role |
| --- | --- | --- |
| Dependency management | uv | Creates the environment, synchronizes the lockfile, and runs project commands. |
| Tests | pytest | Runs unit tests and opt-in Linux/systemd integration tests. |
| Task shortcuts | Make | Provides common setup, test, demo, benchmark, and CLI commands. |
| Benchmarks | Python scenario modules and Linux test services | Measure latency, background progress, controller overhead, and recovery behavior under real contention. |
| Containers | Docker and Compose, where useful | May package reproducible benchmark workloads or supporting services; containers are not the host-control or privilege boundary. |

Typical setup and verification commands are:

```bash
uv sync
uv run pytest
uv run senos --help
```

Tests that alter real resource settings are opt-in and require a suitable Linux
systemd environment. See the [development guide](development.md),
[CPU execution guide](cpu-execution.md), and [experiment guide](experiments.md)
before running them.

## Language-model integration

Natural-language interpretation is isolated behind a replaceable provider
adapter. The current adapters support OpenAI and Gemini APIs, while validated
Pydantic contracts and deterministic policy code remain responsible for the
actual decision and authorization boundaries. A model does not receive direct
shell, systemd, cgroup, or SQLite access and cannot authorize arbitrary host
commands.

Language-model access is optional for direct deterministic CLI workflows. API
credentials are supplied through the local environment and must not be
committed. See the [language-model agent guide](llm-agent.md) for configuration
and limitations.

## Deliberately deferred

The first milestone does not require a web framework, frontend framework,
message broker, distributed-agent framework, external database, Kubernetes, or
a custom kernel component. A future UI should call the same application
services used by the CLI rather than embedding policy logic in presentation
code.

Docker can improve workload reproducibility, but SenOS should not run as a
privileged container by default. The restricted executor and deterministic
safety checks remain the boundary for host resource changes.

## Related documentation

- [Architecture](architecture.md)
- [Shared data contracts](contracts.md)
- [Safety model](safety.md)
- [Linux development setup](development.md)
- [CPU execution and recovery](cpu-execution.md)

