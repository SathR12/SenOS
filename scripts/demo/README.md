# Live demo: an app being starved, then prioritized

Run everything inside the Linux VM (`multipass shell senos`). Use two terminal windows with a large font.

`demo_app.py` is a stand-in "interactive" app. It does real CPU work and shows its live speed as a bar, relative to its fastest second. `start-build.sh` starts a background job with 8 busy loops that competes for the VM's 4 CPUs.

## Steps

**Terminal 1: start the app.** It should show green, about 100%.

```bash
cd ~/SenOS && ./scripts/demo/start-app.sh
```

**Terminal 2: start the background job.** Terminal 1 drops to red, about 60%.

```bash
cd ~/SenOS && ./scripts/demo/start-build.sh
```

**Show what SenOS sees.** Wait about 10 seconds so the pressure average builds up.

```bash
uv run senos status
```

**State the goal.** This validates the request and applies nothing.

```bash
uv run senos intent prioritize web --duration-seconds 600 --preserve build
```

**Ask the CPU agent.** It takes a few seconds and should propose `CPUWeight 100 -> 200`.

```bash
uv run senos agent cpu --target web --preserve build
```

**Apply the proposal by hand.** The executor isn't connected yet. Terminal 1 climbs to about 75% within a few seconds.

```bash
systemctl --user set-property --runtime web.service CPUWeight=200
```

**Show the background job still runs.**

```bash
uv run senos status
```

**Undo the change.**

```bash
systemctl --user set-property --runtime web.service CPUWeight=100
```

**Clean up.** Press Ctrl+C in terminal 1, then run:

```bash
./scripts/demo/cleanup.sh
```

## Rehearsal results (4-CPU Multipass VM)

| Step | App speed | web CPU | build CPU |
|---|---|---|---|
| App alone | 100% | about 300% | none |
| Background job running | about 61% | 188% | 210% |
| After CPUWeight 200 | about 75% | 275% | 123% |

## Notes

- Both units start with `CPUWeight=100`. Without an explicit weight, the live agent currently returns no action.
- `senos restore` only undoes changes SenOS recorded itself, so undo the manual change with `systemctl` as shown.
- If a command says "Current directory does not exist", run `cd ~/SenOS` again.
