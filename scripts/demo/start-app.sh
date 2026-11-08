#!/bin/sh
# Terminal 1: run the demo app as a systemd unit and show its live speed bar.
# Stop it with Ctrl+C.
systemctl --user reset-failed web.service 2>/dev/null
exec systemd-run --user --pty --quiet --unit=web --property=CPUWeight=100 \
    python3 -u "$(cd "$(dirname "$0")" && pwd)/demo_app.py"
