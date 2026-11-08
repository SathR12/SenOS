# Restore recorded CPU settings

Restore is implemented for recorded runtime CPUWeight changes. It does not guess original settings, reset the whole OS, remove registrations, stop applications, or restore memory policies that have not been implemented.

```bash
uv run senos restore demo
uv run senos restore --all
uv run senos restore --all --dry-run
uv run senos restore demo --json
```

Omitting both a name and `--all` restores all pending recorded CPU changes for backward compatibility with the original command. A name plus `--all` is a usage error. `--db PATH` and `SENOS_REGISTRY_PATH` select the same database as registration. Without recorded changes the command succeeds with `No recorded changes to restore.` An unknown workload name is an error.

## What is recorded

The SQLite database includes `cpu_restore_points`, keyed by registered workload ID, with unit, user/system manager, original CPUWeight, systemd invocation ID, cgroup, capture time, restoration time, and last error. A null original weight means systemd reported the setting as unset; it is restored with an empty CPUWeight assignment rather than assuming a value of 100.

The first pending original is retained across repeated captures and process restarts. It is not overwritten by later intermediate values. After successful restoration, a later capture starts a new baseline. This is a minimal recovery journal, not a complete event history for every policy attempt.

## Future executor integration

The current CLI has no command that applies CPU policies. Consequently ordinary registration creates no restore point and provides nothing to undo. The recovery service provides a Python hook for the future authorized executor:

```python
from senos.recovery.restore import RestoreService

service = RestoreService(database_path)
service.capture("demo")  # Must succeed BEFORE changing the registered unit.
```

Capture only reads the unit and persists its original state. A future policy executor must coordinate capture/apply/restore operations so they cannot race; it must not blindly call capture and later apply from independent concurrent workers. Capture failure must prevent policy application. Automatic expiration, a watchdog, autonomous policy decisions, and generalized memory-policy recovery are not part of this change.

## Restore behavior

For each selected pending record, recovery verifies the registration and the active systemd invocation/cgroup. It only issues an allowlisted runtime CPUWeight assignment through the executor backend, then reads systemd again to verify the original configured value. It does not run a shell, invoke sudo, alter permanent unit files, or reset other properties. Commands use `--no-ask-password`, so insufficient system permissions fail without prompting for elevation.

Successful verification marks the record restored. If a write, target check, verification, or journal update fails, recovery data remains pending. Retry is safe if the original setting was applied but the process stopped before recording completion: the next run verifies the current value and finishes the journal entry. `--all` attempts independent workloads even when one fails; any failure returns a nonzero exit status.

`--dry-run` checks targets and prints recorded originals without writing settings, journal state, directories, or databases. JSON results contain name, status (`restored`, `dry_run`, or `failed`), original_cpu_weight, and a message.

A stopped, restarted, or recreated unit is not silently treated as the original invocation. Restoration fails closed and retains recovery data for inspection. This version has no force/discard command for such records; review them manually rather than applying an old policy to a different process instance.

Unregister rejects a workload with a pending CPU restore point. Clear-all rejects the entire deletion if any selected workload has pending recovery. That check and deletion share a SQLite write transaction. Registrations remain after restore.

## Verification

```bash
uv run pytest
SENOS_RUN_SYSTEMD_TESTS=1 uv run pytest
```

The opt-in Linux tests create temporary services owned by the test user, capture originals, apply a temporary test CPU weight, run restore through a separate CLI process, verify numeric and unset restoration, and stop their services in cleanup. Unit tests cover dry-run immutability, stable originals, restarts, partial failures, verification failures, retry, per-target errors, and atomic unregister protection. Tests never modify unrelated services.

See the [systemctl reference](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html) for runtime property assignments and resetting an assignment with an empty value.
