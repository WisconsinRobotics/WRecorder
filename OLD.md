# OLD (Legacy Workflows)

This document contains legacy WRecorder workflows that are no longer the primary path.

## Legacy files

- Single-stream OpenCV: [old/opencv_streamer.py](old/opencv_streamer.py), [old/opencv_receiver.py](old/opencv_receiver.py)
- Demo IP discovery scripts: [old/demo_ip_discovery_broadcaster.py](old/demo_ip_discovery_broadcaster.py), [old/demo_ip_discovery_receiver.py](old/demo_ip_discovery_receiver.py)
- Single-stream GStreamer: [old/gstreamer_streamer.py](old/gstreamer_streamer.py), [old/gstreamer_receiver.py](old/gstreamer_receiver.py)
- GStreamer setup notes: [gstreamer-install.txt](gstreamer-install.txt)

## Single Stream OpenCV Method (Old)

This method uses OpenCV to capture and display video frames, and ZeroMQ (pyzmq) to transmit frames over the network. It supports a single camera stream.

### Launch stream command

Parameters:

- `auto-ip-discovery`: Controls auto IP discovery, either `on` or `off` (default: `off`)
- `discovery-port`: Port for IP discovery packets (must differ from broadcast port) (default: `5556`)
- `discovery-timeout`: Delay in seconds to allow IP discovery (default: `15`)
- `broadcast-port`: Stream port, must match receiver (default: `5555`)
- `camera-id`: Video device index (default: `0`)

Example:

```sh
python3 old/opencv_streamer.py --auto-ip-discovery=on --discovery-timeout=30 --broadcast-port=5555 --camera-id=0
```

### Launch receiver command

Parameters:

- `auto-ip-discovery`: Toggle auto IP discovery (default: `off`)
- `discovery-port`: Port for IP discovery packets (must differ from broadcast port) (default: `5556`)
- `discovery-timeout`: Delay in seconds to allow IP discovery (default: `15`)
- `broadcast-ip`: Manual IP if auto discovery is disabled (default: `0.0.0.0`)
- `broadcast-port`: Stream port, must match broadcaster (default: `5555`)

Example:

```sh
python3 old/opencv_receiver.py --auto-ip-discovery=on --discovery-timeout=30 --broadcast-port=5555
```

## Single Stream GStreamer Method (Old)

This method uses GStreamer to capture, display, and transmit frames. It supports a single stream.

### Launch stream command

Parameters:

- `ip`: Receiver IP, available from `hostname -I`
- `port`: Stream port (must match receiver)
- `video`: Video device index, appended to `/dev/video`

Example:

```sh
python3 old/gstreamer_streamer.py -ip=172.20.10.3 -port=5000 -video=0
```

### Launch receiver command

Parameters:

- `port`: Stream port, must match broadcaster

Example:

```sh
python3 old/gstreamer_receiver.py -port=5000
```

## Legacy troubleshooting notes

- No camera detected: verify device node (for example `/dev/video0`) and permissions.
- Connection refused: verify broadcaster IP/port and firewall rules.
- Auto-discovery fails: verify discovery ports and UDP broadcast support.
