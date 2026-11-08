"""Demo "interactive" app: does real CPU work and shows its live speed.

Run it as a systemd unit so SenOS can observe it, for example:
    systemd-run --user --pty --unit=web --property=CPUWeight=100 python3 scripts/demo/demo_app.py
"""

import multiprocessing as mp
import os
import sys
import time

WORKERS = int(os.environ.get("DEMO_WORKERS", "3"))
CHUNK = 20_000  # loop iterations per unit of work


def worker(counter):
    while True:
        total = 0
        for i in range(CHUNK):
            total += i * i
        with counter.get_lock():
            counter.value += 1


def render(rate, best):
    share = rate / best if best else 0.0
    color = "\033[32m" if share >= 0.85 else "\033[33m" if share >= 0.7 else "\033[31m"
    bar = "█" * int(share * 40)
    return f"{color}Speed {share * 100:5.1f}%  {bar:<40}\033[0m  ({rate:,.0f} units/s)"


def main():
    counter = mp.Value("q", 0)
    for _ in range(WORKERS):
        mp.Process(target=worker, args=(counter,), daemon=True).start()
    tty = sys.stdout.isatty()
    best, last, started = 0.0, 0, time.monotonic()
    while True:
        time.sleep(1)
        now, value = time.monotonic(), counter.value
        rate = (value - last) / (now - started)
        last, started = value, now
        # Speed is relative to the fastest second seen, normally while running alone.
        best = max(best, rate)
        line = render(rate, best)
        print(f"\r{line}" if tty else line, end="" if tty else "\n", flush=True)


if __name__ == "__main__":
    main()
