
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

- Python 3.13 (specifically 3.13)
- GStreamer, GTK, and system dependencies
- pip packages (installed via virtual environment)

### Quick Installation

1. **Install system dependencies, create virtual environment, & install Python packages:**

```sh
make setup
```

If this isn't working, read through the `Makefile` to understand the steps and try running them manually. Common issues include missing GStreamer plugins or Python dependencies.

### Notes

- The `Makefile` will detect your virtual environment (`.venv` or `env`) and use it automatically.
- PyQt6 is now used for the receiver GUI instead of OpenCV's window management.
- Make sure you have an existing OpenCV build with GStreamer support. If you don't, consider building it separately or obtaining a prebuilt wheel.

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