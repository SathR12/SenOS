# Explicit CPU application

CPU execution is opt-in. Existing one-shot commands and monitoring without
`--apply` remain dry-run. The first executor boosts a registered **user-manager**
workload's CPUWeight under sustained contention; it never lowers peer weights,
changes affinity, kills apps, changes memory settings, or runs model-generated code.
A larger relative share can still reduce background progress.

## Run a visible change

Use the [controlled test workloads](live-agent-testing.md) so both services start at
CPUWeight=100, compete on one allowed logical CPU, and use the separate test registry.
Then run in WSL/Linux:

```bash
uv run senos monitor start agent-a --duration-seconds 60 --preserve agent-b --interval 5 --apply --db var/live-agent-test.sqlite3 --monitor-db var/monitor-test.sqlite3
```

Or interpret the objective through the configured LLM once:

```bash
uv run senos agent run "Prioritize agent-a for one minute while keeping agent-b progressing" --watch --apply --db var/live-agent-test.sqlite3 --monitor-db var/monitor-test.sqlite3
```

`agent run --apply` requires `--watch`. Inspect actual settings in another terminal:

```bash
systemctl --user show senos-agent-a.service --property=CPUWeight --value
systemctl --user show senos-agent-b.service --property=CPUWeight --value
```

When pressure and eligibility checks pass, the first value should change from 100
to 200 while the second remains 100. Low pressure or missing controller access
correctly yields no action. Application output includes a verified ActionResult;
model prose or a Proposal is not evidence that a write succeeded.

Ctrl+C, a cycle limit, an external `monitor stop`, or the original deadline ends
monitoring and triggers original-setting restoration and verification. Both settings
should return to their original values. A setting initially unset is restored as
unset, even if its measured kernel baseline was 100. Run the workload cleanup in
the testing guide after finishing. `--json` streams action and recovery data.

## Authorization and persistence

Before application, SenOS resolves registered identities and measures effective
weights. It binds execution to the target's systemd invocation and cgroup, captures
the original configured setting durably, acquires exclusive ownership of that
workload, and starts an independent recovery service that must acknowledge readiness.
If any step fails, no new CPU policy is applied.

Every proposed write is independently re-evaluated against the validated CPUContext.
The executor rechecks the authorized Intent, deadline, registered inventory, live
peer hierarchy, preserved workloads, effective expected weight, and target invocation.
Application/restoration share a reentrant cross-process lock. Pending recovery
continues to block unregistration. Unrelated administrators do not participate in
this lock, so live identity and pre/post-write verification are also necessary.

Changes are boosts only, at most 2x the original measured baseline and at most 1000.
The original-relative ceiling prevents repeated proposals from escalating
100 -> 200 -> 400 -> 800. A persisted 30-second real-change cooldown also applies.
This is a bounded first control policy, not a general optimizer or progress guarantee.
There is no latency-driven adjustment or arbitrary LLM choice of CPU weights.

The existing restore journal holds immutable originals. `cpu_execution_sessions`
records ownership, deadline, controller identity, recovery readiness, and the last
planned weight/change time. The plan is committed before the host write so recovery
can handle a controller crash between write and verification. `cpu_execution_actions`
stores ActionResult audit records. Runtime data remains Git-ignored.

## Independent recovery and limits

Each applying objective launches a uniquely named systemd user recovery service.
It polls approximately once per second and restores when the original deadline
passes or the controller process disappears. Process start identity guards against
PID reuse. Normal completion restores immediately; the worker then exits.
Systemd restarts a failed worker, with a runtime bound providing a limited retry
window. The executor does not depend solely on a Python `finally` block.

Restoration rechecks invocation/cgroup identity. It refuses to overwrite unrelated
external CPU settings automatically, or to write to a restarted application instance.
Such failures remain pending and are reported as failures; they are not successful
restorations. After reviewing the target, use explicit recovery:

```bash
uv run senos restore agent-a --db var/live-agent-test.sqlite3
```

A manual successful restore revokes execution ownership, preventing the running
monitor from applying the objective again. Inspect the user journal for a worker:
`journalctl --user -u senos-recovery-SESSION_ID_WITHOUT_HYPHENS.service`.

This handles controller crashes while the Linux systemd user manager stays alive.
Reboot/login-manager shutdown, inaccessible files, permission changes, or persistent
backend failure can prevent automatic recovery. Runtime systemd properties normally
end with the unit or boot, but stale recovery records still require review. Automatic
boot reconciliation is not implemented. Sleep/suspend and timer polling can delay
wall-clock expiration; no hard real-time deadline guarantee is claimed.

## Measure the result

```bash
uv run python -m benchmarks.scenarios.cpu_contention --apply --seconds 10 --warmup 10 --repetitions 3
```

This adds `contended_applied` to the observation-only benchmark. It applies only to
its own synthetic services, verifies effective weights, measures latency and
background progress, restores originals before phase cleanup, and records actions
and recovery in the result. Applied comparison ratios exclude trials where no
verified action occurred. Lower applied/default p95 is desirable, but inspect the
background throughput ratio and request failures as well. Synthetic improvements
are not proof that a real browser call will improve.

## Validation

```bash
uv run pytest tests/unit/test_cpu_application.py tests/unit/test_monitor.py
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest tests/integration/test_cpu_application_systemd.py
```

Tests cover applied and restored weights, unset originals, cooldown and ceiling,
conflicting ownership, recovery-worker startup failure, restarted instances, external
configuration changes, write failure, manual restoration, and real Linux SIGKILL
recovery. The LLM is not called by these execution tests.
