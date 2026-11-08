# One-command autoplay game demo

Inside WSL/Linux from the repository root:

```bash
make game
```

Open the printed URL. The ball and paddles autoplay; no input or additional Python
package is required. `make game-ai` uses Gemini to interpret the same one-minute
objective. Default `make game` uses the deterministic path so rehearsals do not
depend on API availability. Both modes explicitly apply real CPU policies.

The game frame loop runs in a separate Python process from its HTTP server, both
inside a dedicated systemd user service. This avoids sharing the frame loop's
Python execution lock with HTTP handling.
A second Python process repeatedly computes SHA-256 batches. Both are pinned to
one allowed logical CPU to construct reproducible contention. The game calibrates
fixed work to about 28 CPU milliseconds once at startup, then keeps that iteration
count unchanged throughout the run. Its ordinary frame cap is 30 FPS; there are
no phase-dependent artificial sleeps or fabricated frame delays.

The browser draws only new Python-produced positions, without interpolation.
Frame time measures consecutive Python frame completions, including scheduling
wait and normal frame pacing. FPS is completed frames divided by the sum of their
intervals. A late frame exceeds the 33.3 ms target by more than 10 percent. The
live readout uses recent frames; the phase table accumulates distinct observed
frames. The initial warmup is excluded. Requests that span a detected weight
transition are kept separately. Phase boundaries are sampled, not exact traces of
individual scheduler events.

The automated sequence is warmup (10 seconds), baseline (15 seconds), a one-minute
objective, and restored measurements (10 seconds). Eligible policy changes boost
the game weight from 100 to 200; the background stays at 100. The original setting
is verified before the owned services stop. The page preserves results for five
minutes after completion, so the animation stopping then is intentional.

Watch the moving ball, FPS, p95 frame time, late-frame fraction, and background
batches/second together. Lower p95 frame times and fewer late frames per measured
frame indicate smoother computation. Results vary with CPU frequency, host load,
and browser/WSLg presentation. The page reports Python-engine FPS, not guaranteed
monitor refresh rate or input latency. This is an autoplay synthetic game workload,
not evidence of improving a commercial game.

In a second terminal, the existing shortcuts work without extra paths:

```bash
make demo-status
make proof
make results
make demo-stop
```

`make results` detects game runs and shows FPS and frame times instead of treating
the monitor's incidental HTTP probe latency as game performance. Proof reads actual
Linux PIDs, command lines, kernel weights, and advancing CPU counters.
`make demo-stop` or Ctrl+C restores settings and stops the owned workloads. The
[existing execution recovery limits](cpu-execution.md) apply. Do not launch the web
and game demos concurrently; they share the experiment lock to avoid contaminating
measurements. Each run saves samples, game frame intervals, configuration, logs,
and recovery status under the ignored `var/web-demo/` directory.
