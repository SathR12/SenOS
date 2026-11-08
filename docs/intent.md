# Deterministic intent command

The first intent command turns explicit CLI options into a validated shared
`Intent` contract. It resolves workload names through the persistent registry.
It does not call a language model, persist a pending objective, create a policy
proposal, or change Linux resource settings.

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
and the guarantee that intent submission does not contact systemd. Linux
integration is not required until intents reach live telemetry or the executor.

Intent validation is separate from `senos agent cpu --target NAME`, which collects live telemetry and proposes a CPU policy in dry-run mode. Submitting an intent does not start monitoring or forward it to the CPU agent. Neither command applies new policies.
