import cv2
import zmq
import base64
import numpy as np
import socket
import argparse
import threading
import signal
from typing import List
import time

def receive_camera_data(ip: str, port: int, stop_event: threading.Event, frames: dict, lock: threading.Lock, stats: dict):
    """Subscribe to a single publisher at ip:port and display frames until stop_event is set."""
    context = zmq.Context()
    footage_socket = context.socket(zmq.SUB)
    footage_socket.setsockopt(zmq.CONFLATE, 1)
    footage_socket.connect(f'tcp://{ip}:{port}')
    footage_socket.setsockopt_string(zmq.SUBSCRIBE, '')
    # set a receive timeout so we can check stop_event periodically
    footage_socket.setsockopt(zmq.RCVTIMEO, 500)

    window_name = f"Stream-{port}"
    print(f"Receiving data on {ip}:{port} -> window '{window_name}'")
    print("Press 'q' in any window to quit")

    try:
        while not stop_event.is_set():
            try:
                frame_bytes = footage_socket.recv()
            except zmq.Again:
                continue

            try:
                with lock:
                    stats[window_name] = stats.get(window_name, 0) + len(frame_bytes)
            except Exception:
                pass

            try:
                img = base64.b64decode(frame_bytes)
                npimg = np.frombuffer(img, dtype=np.uint8)
                source = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
                if source is None:
                    continue
                # store the latest frame for the main thread to display
                try:
                    with lock:
                        frames[window_name] = source.copy()
                except Exception:
                    # if storing fails, skip this frame
                    continue
            except Exception:
                # skip malformed frames
                continue
    finally:
        # remove any stored frame for this window
        try:
            with lock:
                if window_name in frames:
                    del frames[window_name]
                if window_name in stats:
                    del stats[window_name]
        except Exception:
            pass
        try:
            footage_socket.close()
        except Exception:
            pass
        try:
            context.term()
        except Exception:
            pass


def start_multiple_receivers(ip: str, ports: List[int]):
    stop_event = threading.Event()
    threads: List[threading.Thread] = []
    frames: dict = {}
    lock = threading.Lock()
    stats = {}

    for port in ports:
        t = threading.Thread(target=receive_camera_data, args=(ip, port, stop_event, frames, lock, stats), daemon=True)
        t.start()
        threads.append(t)
        print(f"Started receiver for {ip}:{port}")

    return stop_event, threads, frames, lock, stats

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_receiver', description='Receive one or more camera streams over ZMQ')
    parser.add_argument('--broadcast-ip', type=str, default="0.0.0.0", help='IP of the publisher(s)')
    parser.add_argument('--base-port', type=int, default=5555, help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--count', type=int, default=1, help='Number of sequential ports to subscribe to starting at base-port')

    args = parser.parse_args()
    broadcast_ip = args.broadcast_ip
    ports = [args.base_port + i for i in range(args.count)]

    stop_event, threads, frames, lock, stats = start_multiple_receivers(broadcast_ip, ports)

    def _signal_handler(signum, frame):
        print('Stopping receivers...')
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        # Main loop handles displaying frames so GUI calls happen on main thread
        prev_bytes = {}
        prev_time = time.time()
        rate_text = "0 KB/s"
        while any(t.is_alive() for t in threads) and not stop_event.is_set():
            # copy keys to avoid holding lock for long
            with lock:
                keys = list(frames.keys())
            for k in keys:
                with lock:
                    frame = frames.get(k)
                if frame is None:
                    continue

                now = time.time()
                if now - prev_time >= 1.0:
                    with lock:
                        total = stats.get(k, 0)
                    prev = prev_bytes.get(k, 0)
                    rate_bps = (total - prev) / max(1e-6, now - prev_time)
                    prev_bytes[k] = total

                    rate_text = f"{rate_bps/1024:.1f} KB/s"
                    prev_time = now
                cv2.putText(frame, rate_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.imshow(k, frame)
            if cv2.waitKey(30) & 0xFF == ord('q'):
                stop_event.set()
                break
        # wait for threads to finish
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop_event.set()
        for t in threads:
            t.join()

    # destroy any remaining windows
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass

    print('All receivers stopped')