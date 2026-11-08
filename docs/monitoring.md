# Continuous observe-only monitoring

The foreground monitor repeatedly collects live telemetry and evaluates the **same
validated Intent**. It logs decisions until the original deadline, Ctrl+C, an
explicit stop, an error, or a cycle limit. By default it never changes CPU weights or captures restoration data. Explicit
`--apply` enables the [restricted CPU executor](cpu-execution.md) and restoration.
Neither mode calls an LLM during the local loop.

## With the LLM

From the repository root in Linux/WSL, configure the [Gemini/OpenAI agent](llm-agent.md)
and register workloads first:

```bash
uv run senos agent run "Prioritize web for one minute while keeping build progressing" --watch --interval 5
```

The model resolves the objective once using its existing bounded tool loop. If it
returns a validated evaluation, monitoring reuses that first evaluation, preserves
the original Intent ID, workload UUIDs, source text, submission time, and deadline,
and performs subsequent cycles locally. It does not spend API credits every cycle.
Clarification or unsupported results end the command without creating a session.

For a short check, add `--max-cycles 3`. Add `--json` to stream **JSON Lines**, not
one JSON document: an `agent` record, `started`, one `cycle` record per evaluation,
and `finished`. Ordinary one-shot `agent run --json` keeps its existing JSON format.

## Without the LLM

The same loop accepts explicit options, with no API key or provider required:

```bash
uv run senos monitor start web --duration-seconds 60 --preserve build --interval 5

# Bounded two-cycle check with separate experiment databases:
uv run senos monitor start agent-a --duration-seconds 60 --preserve agent-b --interval 1 --max-cycles 2 --db var/live-agent-test.sqlite3 --monitor-db var/monitor-test.sqlite3
```

Use the [controlled workload setup](live-agent-testing.md) to create and register
`agent-a` and `agent-b` for the second command. Stop those services using that guide's
cleanup commands after testing. Missing names fail before creating a monitoring run.

Each cycle normally takes several seconds to collect three fresh telemetry rounds.
`--interval` is the **additional pause after a cycle**, from 1 through 60 seconds,
not a promised sampling frequency. It defaults to five seconds. Cycles do not
run concurrently or accumulate a backlog. The optional cycle limit is 1 through
10000; a hard limit of 10000 also bounds unattended runs. Reaching it yields
`limited`, not a claim that the objective expired. Wall-clock rollback cannot
extend the lifetime past the monotonic remaining-time budget captured at startup.

## Inspect and stop

The startup line prints the run ID and monitor database path. From another terminal:

```bash
uv run senos monitor list
uv run senos monitor show RUN_ID --limit 20
uv run senos monitor stop RUN_ID
```

For a custom history file, add `--monitor-db var/monitor-test.sqlite3` to each
inspection/stop command. Those commands print JSON. `show` returns the session and
the latest 20 recorded cycles by default, capped at 100 per request.

The monitor polls for stop during its pause approximately once per second. An
in-flight telemetry operation can take longer; its result is discarded if a stop
was recorded before event persistence. Stop never terminates the workload. Ctrl+C
marks a running session `interrupted`; deadline completion marks it `expired`.
Collection or persistence failures stop the loop and report an error. Already
finished sessions keep their terminal status when stopped again.

## What is stored

By default, history lives in `monitor.sqlite3` beside the selected registry:

- Default registry: `~/.local/state/senos/registry.sqlite3` (or absolute `XDG_STATE_HOME`).
- Default monitor history: `~/.local/state/senos/monitor.sqlite3`.
- Custom registry: history defaults to that registry's parent plus `monitor.sqlite3`.

`--monitor-db PATH` overrides history storage. The history file must differ from
the registry; observe-only monitoring does not write registry bytes. Application mode writes recovery and action records there. Missing history is not
created by list/show. History files are local runtime SQLite data and ignored by Git.

`monitor_runs` stores the Intent, registry path, original deadline, start/update
times, status, reason, and cycle count. `monitor_events` stores each cycle's full
read-only evaluation: observations, current weights, warnings, decision, and
optional Proposal. Events append atomically with the cycle count. Observe-only results include
`mode: dry_run` and `executed: false`; apply-mode cycles include ActionResult and actual execution state. User intent and workload metadata are local
sensitive data; keep history private. Retention/pruning is not implemented.

Repeated proposals such as `100 -> 200` do not mean a change happened repeatedly.
Actual weights remain unchanged and proposal-only activity does not start an
application cooldown. Low pressure, inactive targets, and unavailable measurements
can produce `no_action`. A removed/re-registered workload invalidates the old UUID
and ends evaluation rather than retargeting by display name. Changes during an
individual collection still trigger the existing collector's identity guards.

## Limits and next execution milestone

This is a foreground observer, not a background daemon. Closing or forcibly killing
it stops observation. A hard kill may leave a saved `running` record: saved status
is not proof of a live process. Use `monitor stop` to close that record. There is
no automatic monitor resume or boot reconciliation. Applying sessions have an
independent deadline/controller-crash recovery worker, documented in [CPU execution](cpu-execution.md). The saved objective is for inspection, not
authorization to resume host operations.

Explicit CPU application is now available with `--apply`: recheck registration,
invocation, effective weight, bounds and expiration; persist original settings
before the write; serialize application/restoration; verify results; persist real
change cooldowns; and add automatic deadline/restart recovery. Then extend the
benchmark to measure responsiveness and background-progress tradeoffs. The current
policy remains bounded and heuristic; application-level feedback optimization is future work.

## Verification

```bash
uv run pytest tests/unit/test_monitor.py
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest tests/integration/test_monitor_systemd.py
```

Tests cover changing local decisions, unchanged intent/deadline, separate history,
expiration including clock rollback and slow collection, explicit stop races,
Ctrl+C, preserved events on failure, no per-cycle model calls, JSON Lines output,
and real Linux CLI cycles with unchanged workload settings and registry bytes.

## Explicit CPU application

Use `monitor start ... --apply` or `agent run ... --watch --apply` to authorize bounded user-unit CPU changes. Original settings are captured before a write and restored on exit. An independent systemd service handles the deadline or controller crash. See [execution setup, validation, and limitations](cpu-execution.md). Dry-run remains the default.
