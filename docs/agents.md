# First agent: CPU proposals

The first agent is a deterministic Python module, not an LLM service. It uses the existing Workload, Observation, Intent, and Proposal contracts. It is built and tested independently of the live telemetry collector. `senos status` now supports live Linux readings; the agent runner still requires explicit demo/file inputs and is not automatically connected to that collector. No additional runtime dependencies are required.

## Use the small custom harness

```bash
uv run senos agent cpu --demo
uv run senos agent cpu --demo --json
uv run senos agent cpu --input /path/to/context.json --json
```

Exactly one input source is required. Demo inputs are synthetic. A file is caller-provided data, not independently verified telemetry or proof that its workloads are registered. Output always says `mode: dry_run` and `source: demo` or `file`. Nothing calls systemd, changes a database, captures restore settings, or applies proposals.

The harness validates a CPUContext and invokes CPUAgent.evaluate. There is no daemon, agent-to-agent messaging system, model provider, or automatic control loop. The CLI only parses inputs and renders outputs; the agent and runner can be reused independently.

## Inputs and outputs

CPUContext contains:

- `intent`: a shared Intent with target, optional preserved workloads, submitted time, and duration.
- `workloads`: the caller's registered-workload inventory, using shared Workload models.
- `observations`: a history of shared Observation samples.
- `current_cpu_weights`: UUID-keyed measured effective cgroup CPU weights; null means unknown and cannot be guessed. A configured systemd weight may be unset while cgroup cpu.weight still has a readable effective value. Keep the original configured value separately for restore.
- `last_change_at`: optional timestamp for the last actual CPU change, supplied by a future controller.

CPUDecision contains `status` (`proposed` or `no_action`), a reason, and an optional shared Proposal. A proposal expires at the original intent deadline rather than extending its lifetime every time the agent runs.

Example for generating a valid fresh input file without a collector:

```bash
mkdir -p var
uv run python -c "from senos.agents.demo import cpu_context; print(cpu_context().model_dump_json(indent=2))" > var/cpu-context.json
uv run senos agent cpu --input var/cpu-context.json --json
```

Keep generated scenarios/results local. The example remains synthetic even when read from a file. A stale file should produce no action rather than a fresh proposal.

## Initial rule and experimental settings

The agent only considers prioritize intents whose deadline has not passed. The target and preserved workloads must exist in the supplied inventory. It requires three distinct, fresh active target samples spanning at least two seconds, with measured CPU use and CPU PSI `some avg10` at least 5 percent in each sample. It requires another observed active CPU consumer in the same manager with a known weight. Preserved workloads need fresh active observations.

Missing/stale/future samples, unknown weights, elevated target memory pressure (10 percent `some avg10`), and a recent change produce no action. A proposal boosts only the target weight by at most 2x, capped at 1000. A 30-second cooldown is checked against the supplied last-change timestamp. These settings live in CPUPolicy and are project heuristics, not kernel requirements or benchmarked optimal values. Repeated PSI avg10 samples overlap in time; they are a stability check, not independent experimental evidence.

CPU weights govern relative allocation under contention, not guaranteed percentages. Same manager does not prove workloads share the relevant cgroup scheduling parent. The future validator must resolve actual cgroup hierarchy and verify the change can affect the intended competition. See the [kernel cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

The heuristic is a starting baseline, not proof that user-visible latency will improve. Background weights stay unchanged, but increasing a competing target's relative share can still reduce background progress. Guarantees require explicit progress/latency measurements and guardrails in the future controller. The agent does not claim those guarantees.

## Why this harness first

A small custom runner makes decisions reproducible and keeps monitoring inexpensive. No language-model calls occur in the periodic decision path. Begin with this measurable baseline before adding a general agent framework.

A future natural-language adapter can interpret a user request into Intent. Optional LLM advice can be added through a narrow adapter after evaluating it against this baseline. It must produce validated structured output and pass independent safety checks. The prompt template in `src/senos/agents/prompts/cpu_advisor.md` is a design starting point; no current command executes it.

## Before automatic execution

A future controller must fetch real observations and current weights, verify registration against the real registry, resolve cgroup/instance identity, validate proposals and preserved-workload constraints, enforce cooldowns using persisted action history, serialize capture/apply/restore, verify effects, enforce expiration, and recover after failure. The existing restore hook is available, but there is no automatic apply pipeline yet.

## Files and tests

- `src/senos/agents/cpu.py`: validated context, policy, decision, and pure proposal logic.
- `src/senos/agents/demo.py`: synthetic contention scenario.
- `src/senos/agents/runner.py`: minimal input/output harness.
- `src/senos/agents/prompts/cpu_advisor.md`: optional future advisor prompt, currently unused.
- `tests/unit/test_cpu_agent.py`: decision and CLI behavior, including no OS/executor access.

Run `uv run pytest`. The existing opt-in systemd tests exercise registration and restore, not agent effectiveness. Effectiveness must be evaluated later against Linux defaults and a static policy with real latency and background-progress measurements.
