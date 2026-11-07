# Shared data contracts

SenOS components exchange immutable Pydantic models from `senos.contracts`.
Unknown fields are rejected so a producer and consumer cannot silently disagree
about a payload. All timestamps must include a timezone, all identifiers are
UUIDs, and all byte values use bytes rather than platform-dependent units.

## Workload

`Workload` is the allowlist record for an application the user permits SenOS to
observe and manage. It contains a stable ID, a user-facing name, a validated
systemd `.service` unit name, and the registration time. Possessing a process ID
or filesystem path is not enough to create a managed workload.

## Observation

`Observation` is a point-in-time health snapshot for one workload. It records
the systemd lifecycle state, CPU use, memory use, and optional ten-second CPU
and memory PSI averages. CPU use may exceed 100 percent because one workload
can use more than one logical CPU. PSI percentages remain bounded from 0 to 100.

## Intent

`Intent` is a validated statement of what the user wants. The first milestone
supports `prioritize` and `restore`. A prioritize intent identifies one target,
the workloads that must continue making progress, and a bounded duration. A
restore intent has no duration. `source_text` preserves the original request for
audit and explanation.

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
