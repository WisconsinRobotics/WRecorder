# WRecorder
This repo contains the startup code for broadcasting & receiving the camera.

# OpenCV Method (Recommended)

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

# GStreamer Method

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