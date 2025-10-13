
# WRecorder

WRecorder contains simple scripts to broadcast and receive camera streams between computers. It was written to be easy to run on Raspberry Pi devices (camera source) and on desktop receivers.

Goals:
- Provide a lightweight OpenCV-based multi-camera streaming setup (recommended).
- Keep older single-stream OpenCV and GStreamer examples for compatibility.

Contents:
- Multi-stream (OpenCV, recommended): `camera_streamer.py`, `camera_receiver.py`, `launch.sh`
- Single-stream (OpenCV, legacy): `opencv_streamer.py`, `opencv_receiver.py`, `demo_ip_discovery_broadcaster.py`, `demo_ip_discovery_receiver.py`
- Single-stream (GStreamer, legacy): `gstreamer_streamer.py`, `gstreamer_receiver.py`, `gstreamer-install.txt`

## Prerequisites

- Python 3.7+ (use system Python or a virtualenv)
- OpenCV Python bindings (cv2). The minimal required packages are shown below.
- For GStreamer examples, GStreamer and its Python bindings are required (see `gstreamer-install.txt`).

Recommended pip install for machines that will run the streamer Python scripts:

```sh
python3 -m pip install --user opencv-python-headless pyzmq
```


Recommended pip install for machines that will run the receiver Python scripts:

```sh
python3 -m pip install --user opencv-python pyzmq
```

On Raspberry Pi (if using the Pi Camera or /dev/video devices) you may also need:

```sh
sudo apt update
sudo apt install -y v4l-utils gstreamer1.0-tools gstreamer1.0-plugins-good
```

Note: The repo does not include a requirements.txt. If you want a reproducible environment, create a virtualenv and pin package versions.

## Launch stream command
**Parameters**

*base-port*: Controls the base port that the data will be broadcast through, each stream will increment from this port -- `default: 5555`

*camera-ids*: Space-separated list of video device index numbers -- `default: 0`

**Example Command**
```
python3 camera_streamer.py --base-port 5555 --camera-ids 0 2 4
```
This command will launch 3 streams on ports 5555, 5556, and 5557 from the cameras with IDs 0, 2, and 4.

## Launch receiver command
**Parameters**

*broadcast-ip*: IP of the computer that is broadcasting the data -- `default: 0.0.0.0`

*base-port*: Controls the base port that the data will be received through, each stream will increment from this port -- `default: 5555`

*count*: Number of camera streams to receive -- `default: 1`

**Example Command**
```
python3 camera_receiver.py --broadcast-ip 192.168.1.227 --base-port 5555 --count 3
```
This command will receive 3 streams on ports 5555, 5556, and 5557 from the broadcasting computer with IP 192.168.1.227.

# Single Stream OpenCV Method (Old)
Associated files: `opencv_streamer.py`, `opencv_receiver.py`, `demo_ip_discovery_broadcaster.py`, `demo_ip_discovery_receiver.py`

## Launch stream command
**Parameters**

*auto-ip-discovery*: Controls auto IP discovery, either `on` or `off` -- `default: off`

*discovery-port*: Port on which the IP discovery packets will be sent(must be different than the broadcasting port) -- `default: 5556`

*discovery-timeout*: Delay (in seconds) to allow discovery of the IP -- `default: 15`

*broadcast-port*: Port that the data will be received through, must be the same for the corresponding broadcasting command -- `default: 5555`

*camera-id*: The video device index number -- `default: 0`

**Example Command**
```
python3 opencv_streamer.py --auto-ip-discovery=on --discovery-timeout=30 --broadcast-port=5555 --camera-id=0
```


## Launch receiver command
**Parameters**

*auto-ip-discovery*: Toggle if auto ip discovery is enabled -- `default: off`

*discovery-port*: Port on which the IP discovery packets will be sent(must be different than the broadcasting port) -- `default: 5556`

*discovery-timeout*: Delay (in seconds) to allow discovery of the IP -- `default: 15`

*broadcast-ip*: IP to use if auto IP discovery is disabled -- `default: 0.0.0.0`

*broadcast-port*: Port that the data will be received through, must be the same for the corresponding broadcasting command -- `default: 5555`

**Example Command**
```
python3 opencv_receiver.py --auto-ip-discovery=on --discovery-timeout=30 --broadcast-port=5555
```

# Single Stream GStreamer Method (Old)
Associated files: `gstreamer_streamer.py`, `gstreamer_receiver.py`, `gstreamer-install.txt`

##  Command to launch a camera on the raspberry pi using:
**Parameters**

*ip*: IP of the computer that will be receiving the broadcast, accessible through `hostname -I`

*port*: Port that the data will be broadcast through, must be the same for the corresponding receiving command

*video*: The video device index number (will be appended to "/dev/video")

**Example Command**
```sh
python3 gstreamer_streamer.py -ip=172.20.10.3 -port=5000 -video=0
```

## Command to receive camera data on another computer:
**Parameters**

*port*: Port that the data will be received through, must be the same for the corresponding broadcasting command

**Example Command**
```sh
python3 gstreamer_receiver.py -port=5000
```

## Troubleshooting

- No camera detected: confirm device node (e.g., /dev/video0) and permissions. Run `v4l2-ctl --list-devices`.
- Black frames or corrupted frames: try lowering resolution, increase camera warm-up time, or verify codec settings.
- Connection refused / cannot connect: check that the broadcaster IP/port matches the receiver and that any firewalls allow the chosen ports.
- High latency or dropped frames: switch to a lower resolution or reduce frame rate; make sure network bandwidth is sufficient for N streams.
- Auto-discovery fails: ensure discovery ports are different from streaming ports and that UDP broadcasts are allowed on the network.

## Tips & best practices

- Use wired Ethernet for low-latency, reliable multi-camera setups.
- Reserve a contiguous block of ports for the streams to simplify firewall rules (for example 5555..5564 for 10 streams).
- If you outgrow these scripts, consider switching to RTSP, WebRTC, or a dedicated streaming server for more features and reliability.

## Related files

- `launch.sh`: example startup script for automatically launching streamer on boot (edit before use).
- `gstreamer-install.txt`: platform-specific GStreamer installation notes for the GStreamer examples.

## License

This repository doesn't include an explicit license file. Add a LICENSE if you plan to redistribute.

----

If you'd like, I can also:
- Add a `requirements.txt` or `pyproject.toml` to pin Python dependencies.
- Create a small systemd service example to auto-start the streamer on Raspberry Pi boot.
