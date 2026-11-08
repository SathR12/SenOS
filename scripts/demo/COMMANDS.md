# Terminal demo commands

Run from the repository root inside WSL or a Linux VM. A specific Mac,
Multipass hostname, IP address, or credential transfer is not required.

| Terminal | Command | Effect |
| --- | --- | --- |
| 1 | `make terminal-app` | Run foreground CPU work and show throughput |
| 2 | `make terminal-build` | Start competing work and register both demo units |
| 2 | `make terminal-status` | Read actual CPU, memory, and PSI |
| 2 | `make terminal-ai` | Interpret a one-minute objective and apply eligible CPU changes |
| 2 | `make terminal-apply` | Same bounded objective without an API call |
| 3 | `systemctl --user show senos-terminal-web.service -p CPUWeight` | Independently inspect web weight |
| 2 | `make terminal-cleanup` | Restore records and stop owned demo services |

Wait about ten seconds after starting the build so pressure can accumulate.
Choose either AI or direct application, not both concurrently. Wait for the
controller to finish before cleanup. It normally restores the web weight to 100.

The colored speed percentage compares throughput with the fastest second this
process has seen. More CPU cores, host activity, and sampling noise affect it.
The web weight may remain 100 if contention is insufficient; inspect the reported
no-action reason instead of expecting a fixed speedup.

For the browser demo and command-line evidence with automatic registry selection:

```bash
make demo
# Other terminal:
make demo-status
make proof
make results
```

See [terminal setup and safety](README.md) and the [main command guide](../../docs/commands.md).
