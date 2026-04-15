#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python3}"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

"$PYTHON_BIN" "$SCRIPT_DIR/camera_streamer.py" \
    --base-port 4444 \
    --streamer-name cam-pi-1 \
    --auto-find-cameras on \
    --jpg-quality 20 \
    --target-fps 30
