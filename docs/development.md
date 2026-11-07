# Development

This repository currently provides the folder and Python package skeleton. Dependency configuration, a lockfile, runnable CLI commands, and environment preflight are not implemented yet.

Use the [Linux development requirements](../README.md#linux-and-docker-requirements) as the reference. Record the VM image, kernel, systemd version, CPU allocation, memory, and swap configuration before benchmarking.

Next: add `pyproject.toml`, generate `uv.lock`, and implement an observe-only CLI. Keep credentials and local runtime data out of Git.
