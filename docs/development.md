# Development

This repository provides the folder and Python package skeleton, uv dependency configuration, and a CLI with persistent registration, status listing, and deterministic intent validation. Version, registration/unregistration, registry-backed status, and `intent prioritize` commands work. `senos status --demo` provides clearly labeled synthetic samples on Linux, Windows, or macOS. Environment preflight, live telemetry, restore, and resource control are not implemented yet.

Set up with:

```bash
uv sync
uv run pytest
uv run senos --help
```

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Use the [registration walkthrough](registration.md) to verify `senos register` in a WSL/Linux systemd user session. The [shared contracts](contracts.md) are integrated through common validators and an identity-preserving registry adapter. See [status usage](status.md) for saved registrations versus demo readings and [intent usage](intent.md) for deterministic objective validation. Next: add real read-only telemetry, retaining explicit unavailable values when a measurement cannot be collected. Keep credentials and local runtime data out of Git.
