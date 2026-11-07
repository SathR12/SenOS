# Development

This repository provides the folder and Python package skeleton, uv dependency configuration, and a placeholder `senos` CLI. The version, workload-registration, and unregistration commands work; environment preflight, telemetry/status, and resource control are not implemented yet.

Set up with:

```bash
uv sync
uv run pytest
uv run senos --help
```

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Use the [registration walkthrough](registration.md) to verify `senos register` in a WSL/Linux systemd user session. Shared contracts are owned separately; this feature does not introduce a replacement shared model. Next: integrate those contracts and add read-only telemetry/status. Keep credentials and local runtime data out of Git.
