
# WRecorder

WRecorder provides multi-camera streaming between machines using OpenCV + GStreamer over UDP Multicast.

## Active files

- Multi-stream scripts: [camera_streamer.py](camera_streamer.py), [camera_receiver.py](camera_receiver.py)
- Startup scripts: [launch1.sh](launch1.sh), [launch2.sh](launch2.sh)
- Required runtime defaults: [argument_defaults.json](argument_defaults.json)
- Legacy documentation: [OLD.md](OLD.md)

## Runtime defaults (required)

Both scripts require [argument_defaults.json](argument_defaults.json) at runtime with sections `both`, `streamer-only`, `receiver-only`. Missing/invalid files, unknown keys, or missing required keys cause fail-fast startup errors. CLI flags override JSON values.

## Prerequisites

- Python 3.12 (specifically 3.12)
- GStreamer, GTK, v4l2, and other system dependencies

### Building the Project

Because this project relies on GStreamer and custom OpenCV builds to support streaming pipelines, it is highly recommended to use the provided `Makefile` for installation.

Common `Makefile` targets:

- `make deps` — install system packages required for building OpenCV and GStreamer.
- `make source-build-headed` — build and install `opencv-python` from source with GStreamer and GTK enabled (for systems with a display).
- `make source-build-headless` — build and install `opencv-python-headless` from source with GStreamer enabled (headless systems like Raspberry Pi).
- `make prebuilt-headed-x86_64` — download and install a prebuilt x86_64 wheel (if available). This is for the **base station**.
- `make prebuilt-headless-arm64` — download and install a prebuilt arm64 wheel (if available). This is for the **Raspberry Pis**.
- `make check` — verify OpenCV was built with GStreamer support.

Notes:

- The Makefile prefers a virtual environment in `.venv` or `env` and sets `PY`/`PIP` accordingly; activate your venv first to ensure the correct Python is used.
- Building from source can take 15–60+ minutes depending on CPU. Use the prebuilt targets when suitable to skip local compilation.
- If the receiving machine is ARM-based you must use `source-build-headed` as prebuilt wheels are only provided for x86_64 at this time.
- If the streaming machine is x86_64-based but doesn't have a display you must use `source-build-headless` as the prebuilt wheel for x86_64 is built with GTK support which requires a display.

Examples:

Install system dependencies:
```sh
make deps
```

Build OpenCV for a local GUI/debug machine:
```sh
make source-build-headed
```

Build OpenCV for headless devices (e.g., Raspberry Pi):
```sh
make source-build-headless
```

Download and install a prebuilt wheel (x86_64 example):
```sh
make prebuilt-headed-x86_64
```

Download and install a prebuilt wheel (arm64 example):
```sh
make prebuilt-headless-arm64
```

Verify OpenCV GStreamer support:
```sh
make check
```

FYI: make install-* build OpenCV from source which will take a significant amount of time (15-60+ minutes depending on machine).

## Quick start

Start streamer:

```sh
python3 camera_streamer.py
```

Start receiver:

```sh
python3 camera_receiver.py
```

By default, receiver uses `auto-config=on` to auto-discover available streamers and their port ranges. Override discovery with `--auto-config off` and manual flags, or filter by streamer name with `--streamer-name-filter`. Multicast IP is always `224.1.1.1`.

## Camera streamer arguments

| Argument | Description | Default | Expected Input |
|----------|-------------|---------|----------------|
| `--base-port` | Starting port for first camera stream; additional streams use `base-port + index` | `5555` | `1-65535` |
| `--camera-ids` | Space-separated camera IDs | `[0]` | Camera device indices (e.g., `0 2 4`) |
| `--auto-find-cameras` | Auto-detect cameras and override `--camera-ids` | `on` | `on\|off` |
| `--bitrate` | Target H.264 stream bitrate | `250000` | bps (`>= 1`) |
| `--target-fps` | Target stream FPS | `30` | `>= 1` |
| `--simulate-cameras` | Simulate N cameras instead of real devices | `null` (disabled) | `>= 1` or `null` |
| `--streamer-name` | Discovery identity name for receiver filtering | `wrecorder-streamer` | String |
| `--announce-discovery` | Broadcast discovery metadata (streamer name + port range) | `on` | `on\|off` |
| `--discovery-port` | UDP discovery port | `5550` | `1-65535` |
| `--discovery-interval` | Seconds between discovery packets | `1.0` | Seconds (float) |

## Camera receiver arguments

| Argument | Description | Default | Expected Input |
|----------|-------------|---------|----------------|
| `--broadcast-ip` | Legacy Publisher IP (ignored for multicast) | `0.0.0.0` | IP address |
| `--base-port` | Starting port for first subscribed stream | `5555` | `1-65535` |
| `--count` | Number of sequential ports to subscribe to | `1` | Integer `>= 1` |
| `--timeout` | Total setup timeout | `10.0` | Seconds (float) |
| `--auto-config` | Auto-configure from discovery announcements | `on` | `on\|off` |
| `--streamer-name-filter` | Accept only matching streamer name from discovery | `null` (no filter) | String or `null` |
| `--discovery-port` | UDP discovery port | `5550` | `1-65535` |
| `--discovery-timeout` | Discovery phase timeout override | `null` (auto budget) | Seconds (float) or `null` |

## How to connect to different networks on the Pi

```sh
nmcli dev wifi
nmtui
```

## Common examples

Manual streamer setup:

```sh
python3 camera_streamer.py --base-port 5555 --camera-ids 0 2 4 --bitrate 500000 --target-fps 30
```

Discovery receiver with filter:

```sh
python3 camera_receiver.py --auto-config on --streamer-name-filter cam-pi-1
```

Simulated cameras:

```sh
python3 camera_streamer.py --simulate-cameras 4
```

## Troubleshooting

- No camera detected: verify device nodes and permissions, then run `v4l2-ctl --list-devices`.
- Receiver cannot connect: verify stream host/ports and firewall rules.
- Discovery not finding streamer: verify same subnet and matching discovery port.

## Notes

- If a camera is physically disconnected, a streamer restart may still be required depending on device/driver behavior.