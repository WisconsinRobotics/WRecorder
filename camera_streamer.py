import base64
import cv2
import zmq
import time
import argparse
import multiprocessing
import signal
from typing import List
import os


class BroadcastConfig:
    def __init__(self, port: int, camera_id: int, jpg_quality: int):
        self.port = port
        self.camera_id = camera_id
        self.jpg_quality = jpg_quality

def broadcast_camera_data(config: BroadcastConfig, stop_event: multiprocessing.Event):
    # Publish frames from a single camera on tcp://*:{port} until stop_event is set.
    context = zmq.Context()
    footage_socket = context.socket(zmq.PUB)
    footage_socket.setsockopt(zmq.CONFLATE, 1)
    bind_addr = f'tcp://*:{config.port}'
    print(f"[stream-{config.port}] Binding PUB socket to {bind_addr}")
    footage_socket.bind(bind_addr) # 172.20.10.3

    camera = cv2.VideoCapture(config.camera_id)  # init the camera
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    camera.set(cv2.CAP_PROP_FPS, 15)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

    print(f"[stream-{config.port}] Camera {config.camera_id} opened: {camera.isOpened()}")
    frame_count = 0
    last_time = time.time()
    target_fps = 15.0
    frame_interval = 1.0 / target_fps

    try:
        while not stop_event.is_set():
            frame_start = time.time()

            grabbed, frame = camera.read()  # grab the current frame
            frame_count += 1
            if not grabbed or frame is None:
                if frame_count % 50 == 0:
                    print(f"[stream-{config.port}] frame {frame_count}: camera read failed (grabbed={grabbed})")
                time.sleep(0.1)
                continue

            # encode
            encoded, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, config.jpg_quality])
            if not encoded:
                if frame_count % 50 == 0:
                    print(f"[stream-{config.port}] frame {frame_count}: encoding failed")
                continue
            jpg_as_text = base64.b64encode(buffer)

            try:
                footage_socket.send(jpg_as_text)
            except zmq.ZMQError as e:
                print(f"[stream-{config.port}] ZMQ send error: {e}")
                break

            frame_end = time.time()
            elapsed = frame_end - frame_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            
    finally:
        print(f"[stream-{config.port}] cleaning up camera and socket (frames sent: {frame_count})")
        camera.release()
        cv2.destroyAllWindows()
        try:
            footage_socket.close()
        except Exception as e:
            print(f"[stream-{config.port}] error closing socket: {e}")
        try:
            context.term()
        except Exception as e:
            print(f"[stream-{config.port}] error terminating context: {e}")

def start_multiple_streams(base_port: int, camera_ids: List[int], jpg_quality: int):
    ###
    # Start a publisher for each camera_id on ports base_port + index.
    #
    # Returns (stop_event, threads).
    ###
    stop_event = multiprocessing.Event()
    processes: List[multiprocessing.Process] = []

    for idx, cam_id in enumerate(camera_ids):
        port = base_port + idx
        print(f"[main] Starting thread for camera {cam_id} on port {port}")
        broadcast_config = BroadcastConfig(port=port, camera_id=cam_id, jpg_quality=jpg_quality)
        p = multiprocessing.Process(target=broadcast_camera_data, args=(broadcast_config, stop_event), daemon=True)
        p.start()
        processes.append(p)
        print(f"Started camera {cam_id} on port {port}")

    return stop_event, processes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_streamer', description='Streams one or more cameras using OpenCV over ZMQ')
    parser.add_argument('--base-port', type=int, default=5555, help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--camera-ids', type=int, nargs='+', default=[0], help='List of camera IDs to stream (example: --camera-ids 0 2 4)')
    parser.add_argument('--jpg-quality', type=int, default=20, help='Quality of jpgs being transmitted (1-100)')

    args = parser.parse_args()

    base_port = args.base_port
    camera_ids = args.camera_ids
    jpg_quality = args.jpg_quality

    stop_event, processes = start_multiple_streams(base_port, camera_ids, jpg_quality)

    def _signal_handler(signum, frame):
        print('Stopping streams...')
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        stop_event.set()
        for p in processes:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()

    print('All streams stopped')