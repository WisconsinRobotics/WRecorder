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

def receive_camera_data(ip: str, port: int, timeout: int, stop_event: threading.Event, frames: dict, lock: threading.Lock, stats: dict):
    """Subscribe to a single publisher at ip:port and display frames until stop_event is set."""
    context = zmq.Context()
    footage_socket = context.socket(zmq.SUB)
    footage_socket.setsockopt(zmq.CONFLATE, 1)
    footage_socket.connect(f'tcp://{ip}:{port}')
    footage_socket.setsockopt_string(zmq.SUBSCRIBE, '')
    # set a receive timeout so we can check stop_event periodically
    footage_socket.setsockopt(zmq.RCVTIMEO, 500)

    window_name = f"Stream-{port}"
    print(f"\033[93mAttempting to connect to {ip}:{port}...\033[0m")
    
    # Verify connection by waiting for first frame (10 second timeout)
    connection_timeout = timeout
    start_time = time.time()
    first_frame_received = False
    
    while not stop_event.is_set() and not first_frame_received:
        try:
            footage_socket.recv(zmq.NOBLOCK)
            first_frame_received = True
            print(f"\033[92mConnected to {ip}:{port} -> window '{window_name}'\033[0m")
            print("\033[92mPress 'q' in any window to quit\033[0m")
        except zmq.Again:
            if time.time() - start_time > connection_timeout:
                print(f"\033[91mFailed to connect to {ip}:{port} (timeout after {connection_timeout}s)\033[0m")
                return
            time.sleep(0.1)
            continue
    
    if not first_frame_received:
        return

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


def start_multiple_receivers(ip: str, ports: List[int], timeout: int):
    stop_event = threading.Event()
    threads: List[threading.Thread] = []
    frames: dict = {}
    lock = threading.Lock()
    stats = {}

    for port in ports:
        t = threading.Thread(
            target=receive_camera_data, 
            args=(ip, port, timeout, stop_event, frames, lock, stats), 
            daemon=True
        )
        t.start()
        threads.append(t)

    return stop_event, threads, frames, lock, stats

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_receiver', description='Receive one or more camera streams over ZMQ')
    parser.add_argument('--broadcast-ip', type=str, default="0.0.0.0", help='IP of the publisher(s)')
    parser.add_argument('--base-port', type=int, default=5555, help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--count', type=int, default=1, help='Number of sequential ports to subscribe to starting at base-port')
    parser.add_argument('--show-stats', type=str, choices=['on', 'off'], default='off', help='Show streaming statistics')
    parser.add_argument('--timeout', type=int, default=10, help='Seconds to wait for initial connection before giving up')

    args = parser.parse_args()
    broadcast_ip = args.broadcast_ip
    ports = [args.base_port + i for i in range(args.count)]
    show_stats = args.show_stats == 'on'
    timeout = args.timeout

    stop_event, threads, frames, lock, stats = start_multiple_receivers(broadcast_ip, ports, timeout)

    def _signal_handler(signum, frame):
        print('Stopping receivers...')
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        # Main loop handles displaying frames so GUI calls happen on main thread
        prev_bytes = {}
        prev_time = time.time()
        last_data_rate_update_time = time.time()
        data_rate_text = "0 KB/s"
        while any(t.is_alive() for t in threads) and not stop_event.is_set():
            # copy keys to avoid holding lock for long
            with lock:
                keys = list(frames.keys())
            for k in keys:
                with lock:
                    frame = frames.get(k)
                if frame is None:
                    continue
                
                if show_stats:
                    now = time.time()
                    if now - last_data_rate_update_time >= 1.0:
                        with lock:
                            total = stats.get(k, 0)
                        prev = prev_bytes.get(k, 0)
                        rate_bps = (total - prev) / max(1e-6, now - last_data_rate_update_time)
                        prev_bytes[k] = total

                        data_rate_text = f"{rate_bps/1024:.1f} KB/s"
                        last_data_rate_update_time = now
                    cv2.putText(frame, data_rate_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
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