#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
if [ -x "$repo_dir/.venv/bin/python" ]; then
    exec "$repo_dir/.venv/bin/python" "$@"
fi
if command -v uv >/dev/null 2>&1; then
    uv_bin="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv_bin="$HOME/.local/bin/uv"
else
    echo "Install uv and run uv sync inside WSL/Linux first." >&2
    exit 1
fi
exec "$uv_bin" run python "$@"
