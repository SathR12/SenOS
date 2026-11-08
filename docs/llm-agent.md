# Single LLM resource advisor

`senos agent run` uses one LLM with a small read-only tool loop. It interprets a
natural-language objective, discovers registered workloads, inspects live telemetry,
and submits a structured CPU objective to the existing deterministic evaluator.
It can instead ask for clarification or explain that a request is unsupported.
Its model tools never apply CPU settings, restore policies, stop processes, or execute a shell. The separate monitoring executor requires explicit `--watch --apply`; see [CPU execution](cpu-execution.md).

## Configure and run

The agent supports Google Gemini's native generateContent API and OpenAI's Responses API. There is no additional SDK dependency;
the adapter uses Python's standard HTTP client and a fixed HTTPS API endpoint.
Create a Git-ignored `.env` in the repository root:

```dotenv
GEMINI_API_KEY=your_key_here
SENOS_LLM_PROVIDER=gemini
SENOS_GEMINI_MODEL=gemini-3.1-flash-lite
```

An existing shell environment value overrides the corresponding `.env` value.
Only supported provider/key/model settings are loaded. Plain and quoted values,
comments, and optional `export` prefixes work; shell commands and variable
interpolation are never executed. `.env` is read from the current directory only,
so run commands from the project root. Do not put keys in command arguments or Git.
The API call uses your provider account and can incur usage charges.

```bash
uv sync
uv run senos status
uv run senos agent run "Prioritize web for ten minutes while keeping build progressing"
uv run senos agent run "Prioritize web for ten minutes while keeping build progressing" --json
```

Replace `web` and `build` with registered names. To prepare controlled services,
follow the [live-agent walkthrough](live-agent-testing.md), then run:

```bash
uv run senos agent run "Prioritize agent-a for ten minutes while keeping agent-b progressing" --db var/live-agent-test.sqlite3
```

`--db` and `SENOS_REGISTRY_PATH` select the same registry as other commands.
`--provider gemini` or `--provider openai` overrides `SENOS_LLM_PROVIDER`. With no
explicit provider, a Gemini/Google key selects Gemini; otherwise OpenAI is selected.
`--model` overrides the selected provider's model setting. Gemini uses
`SENOS_GEMINI_MODEL` (default `gemini-3.1-flash-lite`); OpenAI uses `SENOS_OPENAI_MODEL`
(default `gpt-4.1-mini`) and `OPENAI_API_KEY`. Model access and billing depend on your
account. `GOOGLE_API_KEY` is accepted as a fallback for Gemini. No key is needed
by deterministic commands or the benchmark. Local/Claude adapters and custom base
URLs are not implemented. The provider interface supports independent additions.

## What the agent does

The model can call these tools, one per turn:

| Tool | Behavior |
| --- | --- |
| `list_workloads` | Return registered names, UUIDs, units, and manager context |
| `inspect_resources` | Read current CPU, memory, pressure, and unit state |
| `evaluate_cpu_intent` | Validate exact workload names and duration, collect fresh evidence, then return a CPU proposal or no action; ends the run |
| `ask_clarification` | Return a question without evaluating; ends the run |
| `decline_request` | Explain an unsupported objective without evaluating; ends the run |

The harness requires discovery and observation before evaluation. It enforces six
model calls at most, strict closed tool arguments, workload registration checks,
a 64-workload inventory limit, response-size bounds, and a 30-second HTTP operation
timeout with no automatic retries. Changed inventory aborts the run. Unknown tools,
malformed responses, invalid targets, duplicate preservation entries, and durations
outside 1 through 86400 seconds fail closed with a readable error.

The model chooses a structured objective and supplies a brief rationale. The
**deterministic CPU evaluator chooses whether to propose and the bounded weight**.
The model cannot override thresholds, fabricate accepted CPU proposals, or provide
commands to the executor. Its rationale is generated text, not proof of benefit.
Review the validated target and duration: semantic misunderstandings remain possible.

Example output ends with a deterministic decision such as:

```text
LLM agent (gemini-3.1-flash-lite): evaluated
Agent rationale: ...
Validated intent: target=...; duration=600 seconds
DRY RUN: proposed: ...
Would set CPUWeight 100 -> 200; expires ...
No resource policy was applied.
```

Low CPU pressure or missing evidence correctly yields `no_action`. An LLM does not
make an otherwise unsupported policy safe or useful. A clarification such as
"Which registered browser workload?" ends this invocation; submit a new complete
request including the clarification. Conversation state is not persisted.
Requests for missing/ambiguous applications, unspecified durations, memory/GPU/network
tuning, multiple priority targets, or throughput guarantees should be clarified or
declined rather than silently reduced to an available CPU objective. These semantic
choices depend on model behavior; the hard tool boundaries are enforced in code.

JSON contains `mode: dry_run`, `executed: false`, `status`, and agent metadata with
model, provider, step count, and tool trace. Evaluated results also contain the model
rationale and the complete authoritative `evaluation`, including the original user
request in Intent source text, live context, warnings, and optional Proposal.
Clarifications and unsupported requests contain a question or reason instead.
Only allowlisted tool calls produce accepted results; arbitrary final model prose
is not treated as an executed action or valid recommendation.

## Data and limits

The user request, registered workload metadata, and requested read-only observations
are sent to the selected provider (Google for Gemini, OpenAI for OpenAI) during the tool loop. No credentials, file contents, environment
dumps, or raw host process listings are exposed as tools. OpenAI requests use `store: false`; Gemini uses stateless generateContent requests.
These choices are not promises about provider retention policies. Gemini model
content, including thought signatures, is replayed unchanged for tool continuation. No transcript is saved
locally by default. `--json` includes workload data and user intent, so keep saved
output under ignored `var/` or outside Git. Never paste secrets into the user request.

The integration follows [official function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling)
, [Google's function-calling reference](https://github.com/googleapis/js-genai/blob/main/docs/index.html#function-calling),
and the [GPT-4.1 mini model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

This is an LLM-assisted dry-run workflow, not an autonomous resource controller.
A [continuous observe-only mode](monitoring.md) is available with `--watch`. It repeats local evaluation without additional model calls. Explicit supervised CPU application and controller-crash/deadline recovery are available via [CPU execution](cpu-execution.md). There is no application-feedback optimizer, automatic boot reconciliation, memory optimizer, or UI. The existing benchmark remains
non-billable and deterministic, providing a baseline for evaluating this agent.

## Tests

```bash
uv run pytest tests/unit/test_llm_agent.py tests/unit/test_llm_provider.py tests/unit/test_gemini_provider.py
```

Offline tests use scripted model responses and controlled telemetry to exercise
the actual tool loop and deterministic pipeline. Provider tests check request
shape, bounds, error redaction, missing configuration, and disabled redirects.
They do not establish live model accuracy or model access for your account.

An optional real-API smoke test creates only its own temporary Linux services,
checks the interpreted names/duration and dry-run result, verifies unchanged
weights and registry, and stops its services. It is nondeterministic and billable:

```bash
SENOS_RUN_SYSTEMD_TESTS=1 SENOS_RUN_LLM_TESTS=1 uv run pytest tests/integration/test_llm_agent_live.py -q
```

Both flags and the selected provider's credentials (environment or local `.env`) are required; ordinary tests never call the API.

## Watch the objective

Add `--watch --interval 5` to keep evaluating the interpreted objective until its original deadline. Use `--max-cycles 3` for a short test. The model runs once; later decisions use local telemetry and deterministic rules. Sessions and cycle history are saved separately from the registry. See [monitoring usage](monitoring.md) for inspection, cancellation, JSON Lines, and crash limitations.
