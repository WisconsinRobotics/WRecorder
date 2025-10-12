#!/bin/sh

cd /WRecorder
source env/bin/activate
python3 camera_streamer.py --base-port 5555 --camera-ids 0 2
cd /