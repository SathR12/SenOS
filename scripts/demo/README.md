# Terminal CPU throughput demo

Wang's terminal app computes real work in multiple Python worker processes and
shows completed units/second. Its colored percentage is relative to the fastest
second observed in that run; it is **not CPU utilization** or HTTP latency.

From the repository root inside Linux/WSL:

```bash
# Terminal 1: foreground work and throughput bar
make terminal-app

# Terminal 2: competing background work, then register both
make terminal-build
# Allow pressure averages to rise for about ten seconds.
make terminal-status
make terminal-ai       # Natural-language objective, one minute, real CPU execution
# Or make terminal-apply for the same structured objective without an API call.
```

The terminal services are named `senos-terminal-web.service` and
`senos-terminal-build.service`. They use a separate ignored registry under
`var/terminal-demo/`. Neither launcher takes over generic `web.service` or
`build.service`. Each service has a 30-minute runtime bound. Cleanup checks this
checkout's explicit ownership marker before stopping a service.

In a third terminal, independently inspect the actual setting:

```bash
systemctl --user show senos-terminal-web.service -p MainPID -p CPUWeight -p CPUUsageNSec
systemctl --user show senos-terminal-build.service -p MainPID -p CPUWeight -p CPUUsageNSec
```

When eligible, the web weight changes 100 -> 200, while the background stays 100.
The restricted executor records originals and verifies restoration when the
one-minute objective ends. Do not manually change weights with systemctl to
present this as a SenOS action: manual changes bypass its journal.

After the controller has finished, use:

```bash
make terminal-cleanup
```

Cleanup restores any recorded changes first and stops only marked demo services.
A failed restoration is reported and stops cleanup rather than claiming success.
Press Ctrl+C in terminal 1 if it remains open.

This demo does not pin CPU affinity. It was originally designed for a small VM;
a machine with many idle cores may show little contention or a correct no-action
decision. No specific slowdown or speedup percentage is guaranteed. For a repeatable
single-core contention setup, browser latency chart, normalized background progress,
and simpler lifecycle, use the [web demo](../../docs/web-demo.md): `make demo`.
The [main command guide](../../docs/commands.md) explains independent verification.
