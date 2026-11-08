#!/bin/sh
# Terminal 2: start the background job that competes with the demo app,
# then register both with SenOS. Run start-app.sh first.
set -e
cd "$(dirname "$0")/../.."
systemctl --user reset-failed build.service 2>/dev/null || true
systemd-run --user --quiet --unit=build --property=CPUWeight=100 \
    sh -c 'for i in 1 2 3 4 5 6 7 8; do (while :; do :; done) & done; wait'
uv run senos register web --unit web.service
uv run senos register build --unit build.service
