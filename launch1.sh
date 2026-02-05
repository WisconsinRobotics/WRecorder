#!/bin/sh

/home/wiscrobo/WRecorder/env/bin/python3 /home/wiscrobo/WRecorder/camera_streamer.py \
    --base-port 4444 \
    --auto-find-cameras on \
    --jpg-quality 20 \
    --target-fps 30
