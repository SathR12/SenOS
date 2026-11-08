# Deterministic intent command

The first intent command turns explicit CLI options into a validated shared
`Intent` contract. It resolves workload names through the persistent registry.
By default it only validates. With `--evaluate`, it collects live telemetry and
returns a deterministic CPU proposal or a no-action decision. Neither mode calls
a language model, persists a pending objective, or changes Linux resource settings.

Register the target and any workloads that must keep progressing before
submitting an intent:

```bash
uv run senos intent prioritize editor \
  --duration-seconds 600 \
  --preserve build
```

`--preserve` may be repeated. Names are exact and case-sensitive, matching the
registry. The target and every preserved workload must already be registered.
The target cannot also be preserved, preserved workloads cannot be repeated,
and duration must be an integer from 1 through 86,400 seconds.

Successful text output ends with:

```text
No resource policy was applied.
```

This makes validation distinguishable from future policy execution. For
machine-readable output, add `--json`:

```bash
uv run senos intent prioritize editor \
  --duration-seconds 600 \
  --preserve build \
  --json
```

The JSON envelope contains `accepted: true`, `executed: false`, and the shared
`intent` payload. `source_text` is a canonical description generated from the
resolved CLI arguments. Use `--db PATH` or `SENOS_REGISTRY_PATH` to select the
same registry used by `register`, `unregister`, and `status`.

The [application service](../src/senos/intent/service.py) handles intent validation; CLI parsing and
rendering remain in the [CLI module](../src/senos/cli.py). The service returns an immutable
`Intent`, allowing a future UI or orchestrator to reuse it. The existing
top-level `restore` command remains the restoration interface for recorded CPU settings rather
than adding a second `intent restore` spelling.

## Verification

```bash
uv run pytest tests/unit/test_intent_command.py
```

Tests cover registry name and UUID lookup, target and preservation resolution,
contract validation, JSON output, readable errors, environment configuration,
and the guarantee that validation-only submission does not contact systemd.
Evaluation tests cover original intent identity and expiration, replaced workload
identities, proposals, no-action decisions, unsupported hosts, and read-only behavior.
Opt-in Linux integration verifies the CLI leaves CPU weights and registry bytes unchanged.

## Evaluate the deterministic path

```bash
uv run senos intent prioritize editor --duration-seconds 600 --preserve build --evaluate
uv run senos intent prioritize editor --duration-seconds 600 --preserve build --evaluate --json
```

Run on Linux with systemd and cgroup v2, using registered workload names. Collection
usually takes a few seconds. The path is structured options -> validated Intent ->
live observations and measured weights -> deterministic CPU decision -> optional Proposal.
Text output shows the reason and proposed weight change, followed by an explicit
no-policy-applied message. Low pressure or insufficient evidence correctly returns
`no_action`; this is a successful evaluation rather than an error.

Evaluation JSON adds `mode: dry_run`, `source: live`, `context`, `warnings`, and
`decision` to the existing `accepted`, `executed`, and `intent` fields. `executed`
remains false, including when a proposal is returned. The original intent ID,
submission time, workload IDs, and deadline are preserved through evaluation.
If a workload was removed and registered again, the old intent is rejected.
Unsupported hosts and invalid registries return a readable error and a nonzero exit.

The reusable [evaluation service](../src/senos/intent/pipeline.py) accepts a shared
Intent and registry path. A future natural-language provider can resolve its output
into that contract and invoke the same service. No provider or API key is implemented.

This is one evaluation, not continuous monitoring. Duration bounds the objective
and proposal; it does not keep the command running. There is no application,
expiration worker, or progress guarantee for preserved workloads. For a controlled
contention experiment, follow the [live-agent testing guide](live-agent-testing.md)
and use the intent command above with that guide's registered names and database.
The separate `senos agent cpu` command remains available for live/demo/file inputs.
