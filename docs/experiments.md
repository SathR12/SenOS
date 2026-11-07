# Experiments

No benchmark results have been collected yet.

Compare Linux defaults, a static policy, and SenOS under the same environment and workload. Record configuration, workload parameters, run duration, repetitions, and raw observations.

Measure interactive p95/p99 latency, background throughput, resource pressure, controller overhead, policy changes, and restoration behavior. Set the acceptable background slowdown before tuning.

Workload programs belong in `benchmarks/workloads/`, experiment definitions in `benchmarks/scenarios/`, and analysis code in `benchmarks/analysis/`. Keep generated results outside tracked source files.
