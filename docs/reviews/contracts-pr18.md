# Local review: shared contracts PR #18

Reviewed commit `c450b98036fa87e194d7f1a8251f4b09f3e28607` from Alex's `feat/shared-data-models` branch against `feat/workload-registration`. This is a local review record, not a submitted GitHub approval. Main and Alex's source branch have not been changed.

## Verification

The combined branch passes 73 tests in WSL, including the opt-in systemd registration/unregistration test. The merge has no textual conflicts. Registration still uses its internal persistence record; passing tests do not demonstrate that registration and telemetry exchange the new contracts yet.

## Findings to resolve before contract integration

### P2: Boolean policy values are silently coerced to integers

`src/senos/contracts/_base.py` defines CpuWeight with a non-strict integer, and `src/senos/contracts/intent.py` defines duration_seconds similarly. Pydantic accepts `proposed_cpu_weight=True` as weight 1 and `duration_seconds=True` as one second. This can turn a malformed model/provider payload into a materially different policy rather than rejecting it. Memory byte counts also use a non-strict integer.

Recommendation: use strict integer fields for CPU weights, durations, and byte counts, with regression cases for booleans, numeric strings, and fractional values. Keep UUID and enum JSON parsing usable; avoid switching the entire model to strict mode without checking those interfaces.

### P2: Workload and registry disagree on supported targets

Registration accepts `.service` and `.scope` targets and display names up to 128 characters. Workload accepts only `.service` and names up to 80 characters. Verified that a registered-style scope and an 81-character name fail Workload validation. Workload also accepts `worker@.service`, a bare template that registration rejects. The shared model has no user/system manager field, while registry identity distinguishes those managers.

Recommendation: agree on a common unit/name subset and whether manager context travels in Workload or must always be resolved from the registry using workload_id. Add an explicit conversion boundary that preserves the existing registry UUID and registration timestamp. Do not construct a new Workload with a new default ID for each read. Both user and system units with the same name must resolve unambiguously. Add tests covering scope targets, maximum-length names, bare templates, and manager identity.

### P2: Unavailable metrics cannot be represented honestly

Observation requires numeric CPU and memory measurements even for `inactive`, `failed`, and `unknown` states. Verified that an inactive observation with `None` metrics is rejected. A stopped unit can lose its cgroup, permissions can deny a read, and rate-based CPU usage needs two samples. Consumers must not fill in zero to mean unavailable.

Recommendation: allow unknown/unavailable measurements explicitly (for example, optional values with documented semantics) or define a separate unavailable-observation result. Confirm how memory is measured (cgroup current versus process RSS) and that PSI scope is documented before implementing the collector.

## Disposition

Alex's implementation is merged locally for continued development without silently changing his contract design. The findings above remain open; this is not a recommendation to merge PR #18 into main unchanged. Agree on contract semantics with Alex before wiring registry/status/telemetry to them. The existing CLI functionality continues to work independently.
