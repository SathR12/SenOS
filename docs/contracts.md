# Shared data contracts

SenOS components exchange immutable Pydantic models from `senos.contracts`.
Unknown fields are rejected so a producer and consumer cannot silently disagree
about a payload. All timestamps must include a timezone, all identifiers are
UUIDs, and all byte values use bytes rather than platform-dependent units.

## Workload

`Workload` is the allowlist record for an application the user permits SenOS to
observe and manage. It contains a stable ID, a user-facing name, a validated
systemd `.service` or `.scope` unit name, the `user`/`system` manager, and the registration time. Possessing a process ID
or filesystem path is not enough to create a managed workload. Display names use
1 through 128 printable characters. Simple units and instantiated templates are
supported; paths, shell syntax, wildcard patterns, escaped names, and bare
uninstantiated templates are rejected. Syntax validation does not authorize an
operation or prove a unit exists.

The registry and contract use the same name/unit validators.
`RegisteredWorkload.to_workload()` preserves the stored UUID, timestamp, and
manager rather than generating a new workload identity. The registry's CLI JSON
continues to use `id`; the shared contract uses `workload_id`. The adapter is the
explicit boundary between those representations. User-manager identity is local
to the OS account owning the registry; do not share a registry across accounts.

## Observation

`Observation` is a point-in-time health snapshot for one workload. It records
the systemd lifecycle state, CPU use, memory use, and optional ten-second CPU
and memory PSI averages. CPU use may exceed 100 percent because one workload
can use more than one logical CPU. CPU and memory measurements default to
`None` when unavailable (including initial rate samples); zero means measured
zero. Memory usage is cgroup `memory.current` in bytes, not summed process RSS.
PSI fields describe the workload cgroup's ten-second pressure averages, not
system-wide pressure. PSI percentages remain bounded from 0 to 100.

## Intent

`Intent` is a validated statement of what the user wants. The first milestone
supports `prioritize` and `restore`. A prioritize intent identifies one target,
the workloads that must continue making progress, and a bounded duration of 1 through 86,400 seconds (an initial SenOS policy choice). A
restore intent has no duration. `source_text` preserves the original natural-
language request or a canonical description of structured CLI input for audit
and explanation.

## Proposal

`Proposal` is the policy change SenOS wants the restricted executor to make.
The initial allowlisted action is `set_cpu_weight`. A proposal records the
expected current weight, proposed weight, rationale, target workload, source
intent, and expiration. CPU weights use systemd's cgroup-v2 range of 1 through
10,000. The proposed value must be different, and every proposal must expire.

## ActionResult

`ActionResult` records what actually happened after the executor received a
proposal. Its status is `applied`, `rejected`, or `failed`, and its reason is
always recorded. An applied result must include the previous value and the
verified effective value. A rejected result may record the value it observed,
but it cannot claim an effective changed value. Failed results may include values
when the executor learned them, allowing
recovery code to represent partial or uncertain failures without inventing data.

## Numeric inputs and compatibility

CPU weights, durations, and byte counts require actual integers. Booleans,
numeric strings, and floats are rejected rather than coerced. UUIDs, timestamps,
and enum values retain their normal JSON parsing behavior.

Existing stored registry records need no migration: the adapter maps existing
columns to the shared contract. `manager` defaults to `user` for older standalone
Workload payloads; records from the registry always supply their explicit
manager. Consumers of Observation must handle `None` instead of assuming every
sample contains numeric metrics.
