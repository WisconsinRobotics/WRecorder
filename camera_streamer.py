import base64
import cv2
import zmq
import socket
import time
import argparse
import subprocess
import ipaddress
import threading
import signal
from typing import List

def broadcast_camera_data(port: int, camera_id: int, stop_event: threading.Event):
    # Publish frames from a single camera on tcp://*:{port} until stop_event is set.
    context = zmq.Context()
    footage_socket = context.socket(zmq.PUB)
    bind_addr = f'tcp://*:{port}'
    print(f"[stream-{port}] Binding PUB socket to {bind_addr}")
    footage_socket.bind(bind_addr) # 172.20.10.3

    camera = cv2.VideoCapture(camera_id)  # init the camera
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)

    print(f"[stream-{port}] Camera {camera_id} opened: {camera.isOpened()}")
    frame_count = 0
    try:
        while not stop_event.is_set():
            grabbed, frame = camera.read()  # grab the current frame
            frame_count += 1
            if not grabbed or frame is None:
                if frame_count % 50 == 0:
                    print(f"[stream-{port}] frame {frame_count}: camera read failed (grabbed={grabbed})")
                time.sleep(0.1)
                continue

            # encode
            encoded, buffer = cv2.imencode('.jpg', frame)
            if not encoded:
                if frame_count % 50 == 0:
                    print(f"[stream-{port}] frame {frame_count}: encoding failed")
                continue
            jpg_as_text = base64.b64encode(buffer)

            # debug info: size
            if frame_count % 30 == 0:
                print(f"[stream-{port}] frame {frame_count}: encoded size={len(jpg_as_text)} bytes")

            try:
                footage_socket.send(jpg_as_text)
            except zmq.ZMQError as e:
                print(f"[stream-{port}] ZMQ send error: {e}")
                break
    finally:
        print(f"[stream-{port}] cleaning up camera and socket (frames sent: {frame_count})")
        camera.release()
        cv2.destroyAllWindows()
        try:
            footage_socket.close()
        except Exception as e:
            print(f"[stream-{port}] error closing socket: {e}")
        try:
            context.term()
        except Exception as e:
            print(f"[stream-{port}] error terminating context: {e}")


def start_multiple_streams(base_port: int, camera_ids: List[int]):
    ###
    # Start a publisher for each camera_id on ports base_port + index.
    #
    # Returns (stop_event, threads).
    ###
    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    for idx, cam_id in enumerate(camera_ids):
        port = base_port + idx
        print(f"[main] Starting thread for camera {cam_id} on port {port}")
        t = threading.Thread(target=broadcast_camera_data, args=(port, cam_id, stop_event), daemon=True)
        t.start()
        threads.append(t)
        print(f"Started camera {cam_id} on port {port}")

    return stop_event, threads

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_streamer', description='Streams one or more cameras using OpenCV over ZMQ')
    parser.add_argument('--base-port', type=int, default=5555, help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--camera-ids', type=int, nargs='+', default=[0], help='List of camera IDs to stream (example: --camera-ids 0 1 2)')

    args = parser.parse_args()

    base_port = args.base_port
    camera_ids = args.camera_ids

    stop_event, threads = start_multiple_streams(base_port, camera_ids)

    def _signal_handler(signum, frame):
        print('Stopping streams...')
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        for t in threads:
            while t.is_alive():
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
        for t in threads:
            t.join()

    print('All streams stopped')