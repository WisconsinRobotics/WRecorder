#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/env/bin/python3}"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

"$PYTHON_BIN" "$SCRIPT_DIR/camera_streamer.py" \
    --base-port 5555 \
    --streamer-name cam-pi-2 \
    --auto-find-cameras on \
    --jpg-quality 20 \
    --target-fps 30
