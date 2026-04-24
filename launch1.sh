#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/env/bin/python3}"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

WAIT_SECONDS="${NETWORK_WAIT_SECONDS:-30}"
READY_IP=""

while [ "$WAIT_SECONDS" -gt 0 ]; do
    READY_IP="$(ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
    if [ -n "$READY_IP" ]; then
        break
    fi
    sleep 1
    WAIT_SECONDS=$((WAIT_SECONDS - 1))
done

# Allow explicit pinning; otherwise use eth0 IPv4 when available.
if [ -n "${WRECORDER_STREAMER_IP:-}" ]; then
    export WRECORDER_STREAMER_IP
elif [ -n "$READY_IP" ]; then
    export WRECORDER_STREAMER_IP="$READY_IP"
fi

"$PYTHON_BIN" -u "$SCRIPT_DIR/camera_streamer.py" \
    --base-port 4444 \
    --streamer-name cam-pi-1 \
    --auto-find-cameras on \
    --jpg-quality 20 \
    --target-fps 30
