# WRecorder
This repo contains the startup code for broadcasting & receiving the camera through GStreamer.

## Command to launch a camera on the raspberry pi:
**Parameters**
*ip*: IP of the computer that will be receiving the broadcast, accessible through `hostname -I`

*port*: Port that the data will be broadcast through, must be the same for the corresponding receiving command

*video*: The video device index number (will be appended to "/dev/video")

**Example Command**
```sh
python start_cam.py -ip=172.20.10.3 -port=5000 -video=0
```

## Command to receive camera data on another computer:
**Parameters**
*port*: Port that the data will be received through, must be the same for the corresponding broadcasting command

**Example Command**
```sh
python start_receiver.py -port=5000
```

