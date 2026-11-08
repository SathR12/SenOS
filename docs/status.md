# Workload status

The status command combines the persistent registry with an explicit demo mode. It uses the shared Workload and Observation contracts; there is no second set of status-only data models.

## Saved registrations

```bash
uv run senos status
uv run senos status --json
uv run senos status --db /path/to/registry.sqlite3
```

Default status lists actual saved registrations and does not require a Linux systemd session to read them. It does not collect live telemetry yet. Lifecycle state is `unknown`; CPU and memory are shown as `N/A` in the table and `null` in JSON. A saved registration alone does not prove an app is running.

The table includes name, unit, user/system manager, lifecycle state, CPU percent, and memory in MiB. CPU percent follows the shared contract: 100 percent per occupied logical CPU, potentially more than 100 for a multicore workload. MiB is bytes divided by 1024 squared.

The database path matches register/unregister: `--db` or `SENOS_REGISTRY_PATH` overrides the default under XDG_STATE_HOME or `~/.local/state/senos/`. Status opens an existing database read-only. A missing database produces an empty list without creating directories or a database. Corrupt data or an invalid existing schema produces a readable nonzero error; status does not repair or overwrite it.

## Explicit demo data

```bash
uv run senos status --demo
uv run senos status --demo --json
```

Demo mode preserves Wang's sample editor/build readings for developing the CLI without Linux telemetry. It is labeled `DEMO` in text and `source: "demo"` in JSON. Demo never reads or writes the workload registry. The two example workloads have stable synthetic UUIDs and use the same shared contracts as saved workloads. Their display does not mean they have been registered or launched.

## JSON envelope

JSON contains:

- `source`: `registry` or `demo`.
- `live_telemetry`: currently always false.
- `workloads`: rows containing a shared `workload` object and a matching `observation` object. Both use the same workload_id.

Keep source metadata when consuming/exporting demo values so synthetic readings cannot be mistaken for measurements. Ordinary status never substitutes demo readings for missing live data.

## Implementation and checks

- `src/senos/cli.py`: argument parsing and rendering.
- `src/senos/telemetry/status.py`: status service and snapshot rows.
- `src/senos/telemetry/fake.py`: isolated synthetic provider.
- `src/senos/contracts/`: shared validation and schema definitions.
- `src/senos/workloads/registry.py`: application-level shared-contract access.
- `src/senos/storage/registry.py`: persistent records and read-only listing.

The status tests cover registry data, empty/missing databases, invalid data, JSON identities, explicit demo labeling, no default demo fallback, and registration removal. The opt-in WSL/Linux systemd test also exercises registration, status, and unregistration across separate CLI processes. Live telemetry and resource control remain future work.
