# Workload registration

Implements [issue #2](https://github.com/SathR12/SenOS/issues/2) with the minimal persistent registry needed by the command. Registration records a user's explicit workload selection. It does not launch the selected workload, collect metrics, change priorities, or grant operating-system permissions.

## Try it in WSL or Linux

From the repository root, after `uv sync`, start a temporary user-owned demo service:

```bash
systemd-run --user --unit=senos-demo --collect /usr/bin/sleep 600
uv run senos register demo --unit senos-demo.service
```

Example output (the ID will differ):

```text
Registered demo: senos-demo.service (user, id=...)
```

Repeat the same command to confirm idempotency: it prints `Already registered` and preserves the ID. The unit must still be active on repeated registration. Read-only resolution uses `systemctl show`, not its human-readable status output. See the [systemctl manual](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html).

For machine-readable output:

```bash
uv run senos register demo --unit senos-demo.service --json
```

JSON contains `created` and a `workload` object with `id`, `name`, canonical `unit`, `manager`, and UTC `registered_at`.

Stop the demo when finished:

```bash
systemctl --user stop senos-demo.service
```

Stopping a workload does not delete its registration. Future telemetry and execution must resolve current unit state again; a saved registration is not proof a workload is still running or safe to change.

## Remove registrations

```bash
uv run senos unregister demo
uv run senos unregister --all
```

The first command removes one named record. An unknown name returns a nonzero status. The second removes all registration records after a confirmation prompt; declining or closing input preserves them. Clearing an empty or missing registry succeeds with zero removals and does not create a database.

For scripts, explicitly skip the clear-all confirmation and request JSON:

```bash
uv run senos unregister --all --yes --json
```

Output is `{"removed": N}`. Both commands accept `--db PATH` and `SENOS_REGISTRY_PATH`, matching registration. Supply exactly one name or `--all`; combining them or supplying neither is a usage error.

Unregistering only deletes registry rows. Applications continue running, and stopped/unavailable workloads can also be removed without contacting systemd. Re-registering after removal generates a new ID.

`restore` remains a placeholder. It will eventually undo resource-policy changes while retaining registrations. Before enabling policy execution, unregister must be extended to reject targets with unrestored changes, including atomic rejection of clear-all if any selected workload has pending recovery. The current controller does not apply policies or track such changes, so that guard is not implemented yet.

## Options and storage

- User units are the default. Use `--system` explicitly for a system-manager unit. The command never invokes sudo or changes that unit's settings.
- Default storage is `$XDG_STATE_HOME/senos/registry.sqlite3`, falling back to `~/.local/state/senos/registry.sqlite3` when XDG_STATE_HOME is absent or relative.
- Override storage with `--db PATH` or `SENOS_REGISTRY_PATH`. Parent directories are created after successful resolution. Avoid sharing a registry between OS users or machines.
- Workload display names are case-sensitive and unique in the registry. Leading/trailing whitespace is stripped. The 128-character name limit is an initial SenOS interface choice.
- A canonical `(manager, unit)` target can have only one display name. Unit aliases resolve to the same target. Conflicting names or targets return a nonzero exit status without overwriting existing records.
- Initial syntax supports simple `.service` and `.scope` names, including instantiated templates. Paths, wildcard patterns, shell syntax, bare template definitions, and escaped unit names are rejected.
- Targets must be loaded, active, and have a dedicated non-root cgroup. Arbitrary PIDs and ordinary shell-launched applications are outside this initial command's scope; launch test workloads through systemd first.
- The default database lives outside the repository. If overriding it, keep runtime databases out of Git.

The storage adapter keeps a small persistence record and converts it to the [shared Workload contract](contracts.md), preserving identity, timestamp, and user/system manager. The application-level registry returns shared contracts and supports exact lookup by name or UUID. Registry and contract name/unit validation are shared. Registration JSON retains its existing `id` field. The registry supports unregister as described above; rename is not implemented. `status` lists registrations with unknown metrics until a collector exists; see [status usage](status.md). `restore` remains a placeholder.

## Verification

```bash
uv run pytest
```

Unit tests cover persistence, stable identities, duplicate conflicts, canonical aliases, invalid inputs, unavailable managers, database failures, and CLI output. They mock systemd and can run without a Linux manager.

For the real Linux integration test, use an isolated WSL/Linux development session with a functioning systemd user manager:

```bash
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest
```

This opt-in test starts its own uniquely named temporary sleep service, registers it from two separate CLI processes, verifies persistence, unregisters it, confirms the service is still active, and stops the service in cleanup. The database is in pytest's temporary directory. No CPU or memory policy settings are changed.
