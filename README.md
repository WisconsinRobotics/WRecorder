
# WRecorder

WRecorder provides multi-camera streaming between machines using OpenCV + ZeroMQ.

## Active files

- Multi-stream scripts: [camera_streamer.py](camera_streamer.py), [camera_receiver.py](camera_receiver.py)
- Startup scripts: [launch1.sh](launch1.sh), [launch2.sh](launch2.sh)
- Required runtime defaults: [argument_defaults.json](argument_defaults.json)
- Legacy documentation: [OLD.md](OLD.md)

## Runtime defaults (required)

Both scripts require [argument_defaults.json](argument_defaults.json) at runtime.

- Sections: `both`, `streamer-only`, `receiver-only`
- Behavior: missing/invalid file causes fail-fast startup error.
- Behavior: unknown keys or missing required keys also cause fail-fast startup error.
- Precedence: CLI flags override values from [argument_defaults.json](argument_defaults.json).

## Prerequisites

- Python 3.7+
- Streamer packages:

```sh
python3 -m pip install opencv-python-headless pyzmq
```

- Receiver packages:

```sh
python3 -m pip install opencv-python pyzmq
```

## Quick start

Start streamer:

```sh
python3 camera_streamer.py
```

Start receiver:

```sh
python3 camera_receiver.py
```

Use discovery by default (`announce_discovery=on`, `auto_config=on` in defaults file) or override per run with CLI flags.

## Camera streamer arguments

Current CLI flags for [camera_streamer.py](camera_streamer.py):

- `--base-port`: Starting port for first camera stream; additional streams use `base-port + index`. Current default from [argument_defaults.json](argument_defaults.json): `5555`.
- `--camera-ids`: Space-separated camera IDs (example: `--camera-ids 0 2 4`). Current default from [argument_defaults.json](argument_defaults.json): `[0]`.
- `--auto-find-cameras`: `on|off`; auto-detect cameras and override `--camera-ids`. Current default from [argument_defaults.json](argument_defaults.json): `on`.
- `--jpg-quality`: JPEG quality in range `1-100`. Current default from [argument_defaults.json](argument_defaults.json): `20`.
- `--target-fps`: Target stream FPS, must be `>= 1`. Current default from [argument_defaults.json](argument_defaults.json): `30`.
- `--simulate-cameras`: Simulate `N` cameras instead of real devices (must be `>= 1`). Current default from [argument_defaults.json](argument_defaults.json): `null` (disabled).
- `--streamer-name`: Discovery identity name for receiver filtering. Current default from [argument_defaults.json](argument_defaults.json): `wrecorder-streamer`.
- `--announce-discovery`: `on|off`; broadcast discovery metadata. Current default from [argument_defaults.json](argument_defaults.json): `on`.
- `--discovery-port`: UDP discovery port, valid range `1-65535`. Current default from [argument_defaults.json](argument_defaults.json): `5550`.
- `--discovery-interval`: Seconds between discovery packets. Current default from [argument_defaults.json](argument_defaults.json): `1.0`.

## Camera receiver arguments

Current CLI flags for [camera_receiver.py](camera_receiver.py):

- `--broadcast-ip`: Publisher IP address. Current default from [argument_defaults.json](argument_defaults.json): `0.0.0.0`.
- `--base-port`: Starting port for first subscribed stream; additional streams use `base-port + index`. Current default from [argument_defaults.json](argument_defaults.json): `5555`.
- `--count`: Number of sequential ports to subscribe to, starting at `base-port`. Current default from [argument_defaults.json](argument_defaults.json): `1`.
- `--timeout`: Total setup timeout in seconds (discovery + initial connection). Current default from [argument_defaults.json](argument_defaults.json): `10.0`.
- `--auto-config`: `on|off`; auto-configure from discovery announcements. Current default from [argument_defaults.json](argument_defaults.json): `on`.
- `--streamer-name-filter`: Accept only matching streamer name from discovery. Current default from [argument_defaults.json](argument_defaults.json): `null` (no filter).
- `--discovery-port`: UDP discovery port. Current default from [argument_defaults.json](argument_defaults.json): `5550`.
- `--discovery-timeout`: Discovery phase timeout override in seconds. Current default from [argument_defaults.json](argument_defaults.json): `null` (auto budget).

## How to connect to different networks on the Pi

```sh
nmcli dev wifi
nmtui
```

## Common examples

Manual streamer setup:

```sh
python3 camera_streamer.py --base-port 5555 --camera-ids 0 2 4 --jpg-quality 30 --target-fps 30
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