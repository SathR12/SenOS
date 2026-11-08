# Development

This repository provides the folder and Python package skeleton, uv dependency configuration, and a CLI with persistent registration and status listing. Version, registration/unregistration, and registry-backed status commands work. On Linux, status reads live CPU, memory, and pressure from cgroup v2; on other platforms it lists saved registrations with N/A readings. `senos status --demo` provides clearly labeled synthetic samples on Linux, Windows, or macOS. Environment preflight, restore, and resource control are not implemented yet.

Set up with:

```bash
uv sync
uv run pytest
uv run senos --help
```

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Use the [registration walkthrough](registration.md) to verify `senos register` in a WSL/Linux systemd user session. The [shared contracts](contracts.md) are integrated through common validators and an identity-preserving registry adapter. See [status usage](status.md) for saved registrations versus demo readings. Run the opt-in systemd tests with `SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest` inside a Linux user session. Next: system-wide pressure and the CPU-control executor. Keep credentials and local runtime data out of Git.
