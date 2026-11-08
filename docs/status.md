# Workload status

The status command combines the persistent registry with live Linux telemetry and an explicit demo mode. It uses the shared Workload and Observation contracts; there is no second set of status-only data models.

## Saved registrations

```bash
uv run senos status
uv run senos status --json
uv run senos status --db /path/to/registry.sqlite3
```

Default status lists actual saved registrations. On Linux it then collects live, read-only telemetry for each one (see below). On other platforms, such as macOS, lifecycle state is `unknown` and readings are `N/A` in the table and `null` in JSON. A saved registration alone does not prove an app is running.

The table includes name, unit, user/system manager, lifecycle state, CPU percent, memory in MiB, and the workload's ten-second CPU pressure (`CPU PSI`, cpu.pressure `some avg10`). CPU percent follows the shared contract: 100 percent per occupied logical CPU, potentially more than 100 for a multicore workload. MiB is bytes divided by 1024 squared.

The database path matches register/unregister: `--db` or `SENOS_REGISTRY_PATH` overrides the default under XDG_STATE_HOME or `~/.local/state/senos/`. Status opens an existing database read-only. A missing database produces an empty list without creating directories or a database. Corrupt data or an invalid existing schema produces a readable nonzero error; status does not repair or overwrite it.

## Live telemetry (Linux)

`src/senos/telemetry/linux.py` reads, and never writes:

1. `systemctl --user|--system show` for each saved unit, resolving its current state and cgroup again each time; cgroups can change after registration.
2. From that cgroup under `/sys/fs/cgroup`: `cpu.stat` `usage_usec`, `memory.current`, `cpu.pressure`, and `memory.pressure`.

CPU percent is a rate: `usage_usec` is sampled twice, 0.5 seconds apart, for all workloads at once, so status takes about half a second when any unit is active. 100 percent is one fully used logical CPU.

State follows systemd `ActiveState`: `active`, `inactive` (including units systemd no longer knows), and `failed`; transitional states and systemd read errors are `unknown`. Only active units with a valid dedicated cgroup are measured. A missing file, unparseable value, invalid cgroup path, or CPU counter that decreases mid-sample (a restart) produces `None` for that reading instead of a guess.

## Explicit demo data

```bash
uv run senos status --demo
uv run senos status --demo --json
```

Demo mode preserves Wang's sample editor/build readings for developing the CLI without Linux telemetry. It is labeled `DEMO` in text and `source: "demo"` in JSON. Demo never reads or writes the workload registry. The two example workloads have stable synthetic UUIDs and use the same shared contracts as saved workloads. Their display does not mean they have been registered or launched.

## JSON envelope

JSON contains:

- `source`: `registry` or `demo`.
- `live_telemetry`: true when readings came from Linux telemetry; false on other platforms and in demo mode.
- `workloads`: rows containing a shared `workload` object and a matching `observation` object. Both use the same workload_id.

Keep source metadata when consuming/exporting demo values so synthetic readings cannot be mistaken for measurements. Ordinary status never substitutes demo readings for missing live data.

## Implementation and checks

- `src/senos/cli.py`: argument parsing and rendering.
- `src/senos/telemetry/status.py`: status service and snapshot rows.
- `src/senos/telemetry/linux.py`: read-only systemd and cgroup v2 telemetry.
- `src/senos/telemetry/fake.py`: isolated synthetic provider.
- `src/senos/contracts/`: shared validation and schema definitions.
- `src/senos/storage/registry.py`: persistent records and read-only listing.

The status tests cover registry data, empty/missing databases, invalid data, JSON identities, explicit demo labeling, no default demo fallback, and registration removal. `tests/unit/test_telemetry.py` covers live readings against fake cgroup files, including multicore CPU, non-active states, invalid cgroup paths, missing files, and counter resets. The opt-in WSL/Linux systemd tests exercise registration, status, and unregistration across separate CLI processes, and measure a real busy-loop unit. System-wide pressure and resource control remain future work.
