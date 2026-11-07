# Development

This repository provides the folder and Python package skeleton, uv dependency configuration, and a `senos` CLI whose `status` command shows fake telemetry from `senos.telemetry.fake`, so it runs on macOS. Environment preflight, real telemetry, and the other CLI commands are not implemented yet.

Set up with:

```bash
uv sync
uv run pytest
uv run senos --help
```

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Next: define shared contracts and implement an observe-only CLI. Keep credentials and local runtime data out of Git.
