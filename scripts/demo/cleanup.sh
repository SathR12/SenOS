#!/bin/sh
# Stop the demo units and remove their registrations.
cd "$(dirname "$0")/../.."
systemctl --user stop web.service build.service 2>/dev/null
systemctl --user reset-failed web.service build.service 2>/dev/null
uv run senos unregister web 2>/dev/null
uv run senos unregister build 2>/dev/null
echo "Demo cleaned up."
