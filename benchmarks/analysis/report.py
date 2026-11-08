"""Summaries from raw samples. No claims of policy benefit from dry-run results."""

import argparse
import json
import math
from pathlib import Path
import statistics


def percentile(values: list[float], percent: float) -> float | None:
    """Nearest-rank percentile; empty samples are unavailable, not zero."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def summarize_requests(samples: list[dict], elapsed: float, missed: int) -> dict:
    successful = [sample["latency_ms"] for sample in samples if sample["ok"]]
    return {
        "requests": len(samples), "successes": len(successful),
        "errors": len(samples) - len(successful), "missed_arrivals": missed,
        "elapsed_seconds": elapsed, "successful_requests_per_second": len(successful) / elapsed,
        "p50_ms": percentile(successful, 50), "p95_ms": percentile(successful, 95),
        "p99_ms": percentile(successful, 99),
    }


def comparisons(phases: list[dict]) -> dict:
    """Compare paired trials; use medians of per-trial ratios, not pooled requests."""
    ratios = {"latency_contention_ratio": [], "background_progress_ratio": [], "applied_vs_contended_latency_ratio": [], "applied_vs_contended_background_ratio": []}
    for trial in sorted({phase["trial"] for phase in phases}):
        group = {phase["phase"]: phase for phase in phases if phase["trial"] == trial}
        alone, busy, background = (group.get(name) for name in ("interactive_only", "contended_dry_run", "background_only"))
        if alone and busy:
            baseline = alone["requests"]["p95_ms"]
            observed = busy["requests"]["p95_ms"]
            if baseline and observed is not None:
                ratios["latency_contention_ratio"].append(observed / baseline)
        applied = group.get("contended_applied")
        if applied and busy and (applied.get("execution") or {}).get("status") == "applied":
            if busy["requests"]["p95_ms"] and applied["requests"]["p95_ms"] is not None:
                ratios["applied_vs_contended_latency_ratio"].append(applied["requests"]["p95_ms"] / busy["requests"]["p95_ms"])
            if busy["background_batches_per_second"] and applied["background_batches_per_second"] is not None:
                ratios["applied_vs_contended_background_ratio"].append(applied["background_batches_per_second"] / busy["background_batches_per_second"])
        if background and busy:
            baseline = background["background_batches_per_second"]
            observed = busy["background_batches_per_second"]
            if baseline and observed is not None:
                ratios["background_progress_ratio"].append(observed / baseline)
    return {key: statistics.median(values) if values else None for key, values in ratios.items() if values or not key.startswith("applied_")}


def markdown(result: dict) -> str:
    def display(value):
        return "N/A" if value is None else f"{value:.2f}"

    lines = ["# SenOS CPU contention benchmark", "", f"Status: {result['status']}", "",
        ("Explicit application comparison on synthetic workloads; inspect verified actions and restoration." if result.get("mode") == "application_comparison" else "Observation-only: no proposal was applied. These results do not establish policy benefit."), "",
        "| Trial | Phase | Requests | Errors / missed arrivals | p95 ms | p99 ms | Background batches/s |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for phase in result["phases"]:
        request = phase["requests"]
        lines.append(f"| {phase['trial']} | {phase['phase']} | {request['requests']} | {request['errors']} / {request['missed_arrivals']} | {display(request['p95_ms'])} | {display(request['p99_ms'])} | {display(phase['background_batches_per_second'])} |")
    lines += ["", "## Policy evaluations", ""]
    for phase in result["phases"]:
        evaluation = phase.get("evaluation")
        if evaluation is None:
            continue
        decision = evaluation["payload"]["decision"]
        lines.append(f"- Trial {phase['trial']}: {decision['status']} - {decision['reason']}")
        if decision["proposal"]:
            proposal = decision["proposal"]
            action = phase.get("execution")
            label = "applied and verified" if action and action["status"] == "applied" else "not applied"
            lines.append(f"  CPUWeight: {proposal['expected_cpu_weight']} -> {proposal['proposed_cpu_weight']} ({label}).")
            if phase.get("recovery"):
                lines.append(f"  Restoration: {', '.join(outcome['status'] for outcome in phase['recovery'])}.")
        lines.append(f"  Evaluation: {evaluation['wall_seconds']:.3f} wall seconds; {evaluation['self_and_child_cpu_seconds']:.3f} self/child CPU seconds.")
    lines += ["", "Paired trial comparisons (median ratios):", ""]
    for key, value in comparisons(result["phases"]).items():
        lines.append(f"- {key}: {display(value)}")
    lines += ["", "Contention ratios compare isolated references; applied_vs_contended ratios compare verified application against equal-weight contention. Inspect background costs and errors, not latency alone.",
        "Background batches are fixed-size hash workloads, not application jobs. Missing measurements remain N/A.",
        "Inspect result.json for raw requests, telemetry, proposals, environment, and cleanup errors.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    print(markdown(json.loads(args.result.read_text())))


if __name__ == "__main__":
    main()
