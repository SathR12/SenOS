# Development

This repository provides the folder and Python package skeleton, uv dependency configuration, and a CLI with persistent registration and status listing. Version, registration/unregistration, registry-backed status, and deterministic `intent prioritize` commands work; see [intent usage](intent.md). `senos status --demo` provides clearly labeled synthetic samples on Linux, Windows, or macOS. Recorded CPU-weight restoration is implemented; see [restore usage](restore.md). Live read-only telemetry works on Linux with systemd and cgroup v2. Environment preflight and automatic policy application are not implemented yet.

Set up with:

```bash
uv sync
uv run pytest
uv run senos --help
```

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Use the [registration walkthrough](registration.md) to verify `senos register` in a WSL/Linux systemd user session. The [shared contracts](contracts.md) are integrated through common validators and an identity-preserving registry adapter. See [status usage](status.md) for saved registrations versus demo readings. Live collection returns explicit unavailable values when a measurement cannot be collected. Live observation histories feed the CPU proposal agent; policy execution remains separately controlled. Keep credentials and local runtime data out of Git.

The pure CPU proposal agent remains independent of collection. See the [agent guide](agents.md) and run `uv run senos agent cpu --demo`. The agent can collect live inputs with `--target NAME` or use demo/file inputs. It never applies a policy. Follow the [manual test guide](live-agent-testing.md) before publishing.
