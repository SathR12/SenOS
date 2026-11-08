# Manually test the live CPU agent

Run inside WSL/Linux from this working checkout (`feat/cpu-agent`), after `uv sync`. The live agent only observes and proposes; it never changes CPU weights or records restoration data.

## Start with an existing registered workload

```bash
uv run senos status
uv run senos agent cpu --target busy
uv run senos agent cpu --target busy --json
```

Replace busy with a saved workload name. Collection takes several seconds. Expect a live-input summary and a DRY RUN decision. Zero PSI, insufficient measurements, inactive units, unknown weights, or no eligible competing peer should produce no_action. This is correct behavior, not a failure. JSON includes source=live, mode=dry_run, observations, measured weights, eligible peer IDs, warnings, and the decision.

## Create controlled CPU contention

These commands launch two new test services pinned to the same allowed CPU. Pinning is part of constructing the experiment, not an action performed by SenOS. Both start with explicit CPUWeight=100. Run in a development environment; each service is stopped during cleanup below.

Use a separate ignored test database so existing registrations are not mixed into the experiment:

```bash
mkdir -p var
cpu=$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')

systemd-run --user --unit=senos-agent-a --collect --property=CPUWeight=100   /usr/bin/taskset -c "$cpu" /usr/bin/python3 -c 'while True: pass'
systemd-run --user --unit=senos-agent-b --collect --property=CPUWeight=100   /usr/bin/taskset -c "$cpu" /usr/bin/python3 -c 'while True: pass'

uv run senos register agent-a --unit senos-agent-a.service --db var/live-agent-test.sqlite3
uv run senos register agent-b --unit senos-agent-b.service --db var/live-agent-test.sqlite3

# Allow the ten-second pressure averages to rise.
sleep 10
uv run senos status --db var/live-agent-test.sqlite3
uv run senos agent cpu --target agent-a --preserve agent-b --db var/live-agent-test.sqlite3
```

If target pressure stays at least 5 percent across the collected samples, both units are active siblings, and metrics/weights are available, expect a proposed target boost from 100 to 200. The proposal is not applied. If no_action appears, inspect the printed reason, warnings, and PSI rather than lowering guards to force a result.

Confirm both actual settings are unchanged:

```bash
systemctl --user show senos-agent-a.service --property=CPUWeight --value
systemctl --user show senos-agent-b.service --property=CPUWeight --value
```

Both should still print 100. The JSON view can be saved under ignored var/ for review:

```bash
uv run senos agent cpu --target agent-a --preserve agent-b --db var/live-agent-test.sqlite3 --json > var/live-agent-result.json
```

This is a contention/plumbing demonstration, not a benchmark proving latency improvements or preserved background throughput. No resource settings are changed by the agent.

## Cleanup

```bash
systemctl --user stop senos-agent-a.service senos-agent-b.service
uv run senos unregister --all --yes --db var/live-agent-test.sqlite3
```

## Automated checks

```bash
uv run pytest
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest
```

The opt-in live-agent test launches its own temporary services, invokes the real CLI, and verifies that CPU settings and registry bytes remain unchanged. Unit tests cover pressure-based proposals and no-action paths using controlled observations, plus registry, identity, weight, and hierarchy changes.
