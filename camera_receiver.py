import cv2
import zmq
import base64
import numpy as np
import socket
import argparse
import threading
from typing import List
import time
import json
from utils import (
    DISCOVERY_MESSAGE_TYPE,
    DISCOVERY_VERSION,
    apply_required_external_defaults,
    install_stop_signal_handlers,
)


def _rate_limited_warn(counter: int, message: str, every: int = 50):
    if counter == 1 or counter % every == 0:
        print(f"\033[91m{message} (count={counter})\033[0m")

def receive_camera_data(ip: str, port: int, timeout: float, stop_event: threading.Event, frames: dict, lock: threading.Lock, stats: dict, window_prefix: str = "Stream"):
    """Subscribe to a single publisher at ip:port and display frames until stop_event is set."""
    context = zmq.Context()
    footage_socket = context.socket(zmq.SUB)
    footage_socket.setsockopt(zmq.CONFLATE, 1)
    footage_socket.setsockopt(zmq.LINGER, 0)
    footage_socket.connect(f'tcp://{ip}:{port}')
    footage_socket.setsockopt_string(zmq.SUBSCRIBE, '')
    # set a receive timeout so we can check stop_event periodically
    footage_socket.setsockopt(zmq.RCVTIMEO, 500)

    window_name = f"{window_prefix}-{port}"
    print(f"\033[93mAttempting to connect to {ip}:{port}...\033[0m")
    
    # Verify connection by waiting for first frame (10 second timeout)
    connection_timeout = timeout
    start_time = time.time()
    first_frame_received = False
    stat_update_failures = 0
    decode_failures = 0
    frame_store_failures = 0
    
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
            except (TypeError, RuntimeError) as exc:
                stat_update_failures += 1
                _rate_limited_warn(stat_update_failures, f"[{window_name}] stats update failed: {exc}")

            try:
                img = base64.b64decode(frame_bytes)
                npimg = np.frombuffer(img, dtype=np.uint8)
                source = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
                if source is None:
                    decode_failures += 1
                    _rate_limited_warn(decode_failures, f"[{window_name}] frame decode returned None")
                    continue
                # store the latest frame for the main thread to display
                try:
                    with lock:
                        frames[window_name] = source.copy()
                except (TypeError, RuntimeError) as exc:
                    frame_store_failures += 1
                    _rate_limited_warn(frame_store_failures, f"[{window_name}] frame store failed: {exc}")
                    # if storing fails, skip this frame
                    continue
            except (base64.binascii.Error, ValueError) as exc:
                decode_failures += 1
                _rate_limited_warn(decode_failures, f"[{window_name}] malformed frame payload: {exc}")
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
        footage_socket.close()
        try:
            context.term()
        except Exception:
            pass


def discover_stream_config(discovery_port: int, timeout: float, streamer_name_filter: str = None):
    """Listen for UDP discovery heartbeats and return the first matching stream config."""
    receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    receiver_socket.bind(("", discovery_port))
    receiver_socket.settimeout(0.25)

    print(
        f"\033[93mListening for discovery on UDP {discovery_port} for up to {timeout:.1f}s"
        f"{f' (filter={streamer_name_filter})' if streamer_name_filter else ''}...\033[0m"
    )

    deadline = time.time() + max(0.1, timeout)
    try:
        while time.time() < deadline:
            try:
                data, _addr = receiver_socket.recvfrom(4096)
            except socket.timeout:
                continue

            try:
                payload = json.loads(data.decode("utf8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if payload.get("type") != DISCOVERY_MESSAGE_TYPE:
                continue
            if payload.get("version") != DISCOVERY_VERSION:
                continue

            streamer_name = str(payload.get("streamer_name", "")).strip()
            streamer_ip = str(payload.get("streamer_ip", "")).strip()
            base_port = payload.get("base_port")
            stream_count = payload.get("stream_count")

            if streamer_name_filter and streamer_name != streamer_name_filter:
                continue

            if not streamer_ip or not streamer_name:
                continue

            if not isinstance(base_port, int) or not (1 <= base_port <= 65535):
                continue

            if not isinstance(stream_count, int) or stream_count < 1:
                continue

            print(
                f"\033[92mDiscovered '{streamer_name}' at {streamer_ip} "
                f"(base_port={base_port}, streams={stream_count})\033[0m"
            )
            return {
                "streamer_name": streamer_name,
                "streamer_ip": streamer_ip,
                "base_port": base_port,
                "stream_count": stream_count,
            }
    finally:
        receiver_socket.close()

    return None


def start_multiple_receivers(ip: str, ports: List[int], timeout: float, window_prefix: str = "Stream"):
    stop_event = threading.Event()
    threads: List[threading.Thread] = []
    frames: dict = {}
    lock = threading.Lock()
    stats = {}

    for port in ports:
        t = threading.Thread(
            target=receive_camera_data, 
            args=(ip, port, timeout, stop_event, frames, lock, stats, window_prefix), 
            daemon=True
        )
        t.start()
        threads.append(t)

    return stop_event, threads, frames, lock, stats

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_receiver', description='Receive one or more camera streams over ZMQ')
    parser.add_argument('--broadcast-ip', type=str, help='IP of the publisher(s)')
    parser.add_argument('--base-port', type=int, help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--count', type=int, help='Number of sequential ports to subscribe to starting at base-port')
    parser.add_argument('--show-stats', type=str, choices=['on', 'off'], help='Show streaming statistics')
    parser.add_argument('--timeout', type=float, help='Total setup timeout in seconds (discovery + initial connection)')
    parser.add_argument('--auto-config', type=str, choices=['on', 'off'], help='Auto-configure from discovery announcements')
    parser.add_argument('--streamer-name-filter', type=str, help='Only accept discovery packets from this streamer name')
    parser.add_argument('--discovery-port', type=int, help='UDP port used for discovery announcements')
    parser.add_argument('--discovery-timeout', type=float, help='Optional override for discovery phase timeout in seconds')

    try:
        apply_required_external_defaults(parser, "receiver-only")
    except RuntimeError as exc:
        print(f"\033[91m[defaults] {exc}\033[0m")
        exit(2)

    args = parser.parse_args()
    broadcast_ip = args.broadcast_ip
    base_port = args.base_port
    stream_count = args.count
    show_stats = args.show_stats == 'on'
    timeout = args.timeout
    auto_config = args.auto_config == 'on'
    streamer_name_filter = args.streamer_name_filter
    discovery_port = args.discovery_port
    discovery_timeout = args.discovery_timeout

    connection_timeout = timeout
    window_prefix = "Stream"
    setup_start = time.time()

    if auto_config:
        if discovery_timeout is None:
            discovery_budget = min(5.0, timeout * 0.5)
        else:
            discovery_budget = discovery_timeout
        discovery_budget = max(0.1, min(discovery_budget, timeout))

        discovered = discover_stream_config(discovery_port, discovery_budget, streamer_name_filter)
        elapsed = time.time() - setup_start
        connection_timeout = max(0.1, timeout - elapsed)

        if discovered is not None:
            broadcast_ip = discovered["streamer_ip"]
            base_port = discovered["base_port"]
            stream_count = discovered["stream_count"]
            window_prefix = discovered["streamer_name"]
        else:
            print("\033[93mNo matching discovery packet found. Falling back to manual args.\033[0m")

    ports = [base_port + i for i in range(stream_count)]
    if any(port < 1 or port > 65535 for port in ports):
        print(f"\033[91mInvalid port configuration: {ports}\033[0m")
        exit(2)

    print(
        f"\033[93mReceiver config: ip={broadcast_ip}, base_port={base_port}, "
        f"count={stream_count}, connection_timeout={connection_timeout:.1f}s\033[0m"
    )

    stop_event, threads, frames, lock, stats = start_multiple_receivers(broadcast_ip, ports, connection_timeout, window_prefix)

    install_stop_signal_handlers(stop_event.set, "Stopping receivers...")

    try:
        # Main loop handles displaying frames so GUI calls happen on main thread
        prev_bytes = {}
        last_data_rate_update_time = {}
        data_rate_text = {}
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
                    if k not in last_data_rate_update_time:
                        last_data_rate_update_time[k] = now
                        prev_bytes[k] = 0
                        data_rate_text[k] = "0 KB/s"

                    last_update = last_data_rate_update_time[k]
                    if now - last_update >= 1.0:
                        with lock:
                            total = stats.get(k, 0)
                        prev = prev_bytes.get(k, 0)
                        rate_bps = (total - prev) / max(1e-6, now - last_update)
                        prev_bytes[k] = total

                        data_rate_text[k] = f"{rate_bps/1024:.1f} KB/s"
                        last_data_rate_update_time[k] = now
                    cv2.putText(frame, data_rate_text.get(k, "0 KB/s"), (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
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