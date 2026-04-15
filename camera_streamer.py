import base64
import cv2
import zmq
import time
import argparse
import multiprocessing
import json
import socket
import threading
from typing import List
import os
import numpy as np
from utils import (
    DISCOVERY_MESSAGE_TYPE,
    DISCOVERY_VERSION,
    apply_required_external_defaults,
    install_stop_signal_handlers,
)


def _int_in_range(name: str, minimum: int, maximum: int = None):
    def _validator(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed < minimum:
            upper = f" and <= {maximum}" if maximum is not None else ""
            raise argparse.ArgumentTypeError(f"{name} must be >= {minimum}{upper}")
        if maximum is not None and parsed > maximum:
            raise argparse.ArgumentTypeError(f"{name} must be <= {maximum}")
        return parsed

    return _validator


class BroadcastConfig:
    def __init__(self, port: int, camera_id: int, jpg_quality: int, target_fps: int, simulation: bool = False):
        self.port = port
        self.camera_id = camera_id
        self.jpg_quality = jpg_quality
        self.target_fps = target_fps
        self.simulation = simulation


def resolve_local_ip() -> str:
    """Best-effort local IPv4 address for discovery announcements.

    This avoids relying on internet access. It first checks an explicit override,
    then uses local-network route probing, and finally falls back to hostname
    address resolution.
    """

    override_ip = os.environ.get("WRECORDER_STREAMER_IP", "").strip()
    if override_ip:
        try:
            socket.inet_aton(override_ip)
            if not override_ip.startswith("127."):
                return override_ip
        except OSError:
            pass

    # Try route-based interface selection without requiring internet connectivity.
    probe_targets = [
        ("10.255.255.255", 1),
        ("192.168.255.255", 1),
        ("172.31.255.255", 1),
    ]
    for host, port in probe_targets:
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe_socket.connect((host, port))
            candidate_ip = probe_socket.getsockname()[0]
            if candidate_ip and not candidate_ip.startswith("127."):
                return candidate_ip
        except OSError:
            pass
        finally:
            probe_socket.close()

    # Hostname resolution fallback (works on many offline/local-only deployments).
    try:
        host_candidates = socket.gethostbyname_ex(socket.gethostname())[2]
        for candidate_ip in host_candidates:
            if candidate_ip and not candidate_ip.startswith("127."):
                return candidate_ip
    except OSError:
        pass

    return "127.0.0.1"


def announce_stream_config(
    stop_event: multiprocessing.Event,
    streamer_name: str,
    streamer_ip: str,
    base_port: int,
    camera_ids: List[int],
    discovery_port: int,
    discovery_interval: float,
):
    """Broadcast stream configuration over UDP for receiver auto-configuration."""
    if not camera_ids or any((not isinstance(camera_id, int) or camera_id < 0) for camera_id in camera_ids):
        raise ValueError("camera_ids must be a non-empty list of non-negative integers")

    announce_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    announce_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    interval = max(0.1, discovery_interval)
    payload = {
        "type": DISCOVERY_MESSAGE_TYPE,
        "version": DISCOVERY_VERSION,
        "streamer_name": streamer_name,
        "streamer_ip": streamer_ip,
        "base_port": base_port,
        "stream_count": len(camera_ids),
        "camera_ids": camera_ids,
    }

    print(
        f"\033[92m[discovery] Announcing '{streamer_name}' on UDP {discovery_port} "
        f"(base_port={base_port}, streams={len(camera_ids)})\033[0m"
    )
    print(
        f"\033[92m[discovery] payload: streamer_ip={streamer_ip}, camera_ids={camera_ids}, "
        f"interval={interval:.2f}s, version={DISCOVERY_VERSION}\033[0m"
    )
    
    try:
        while not stop_event.is_set():
            payload["announced_at"] = time.time()
            packet = json.dumps(payload).encode("utf8")
            try:
                announce_socket.sendto(packet, ("255.255.255.255", discovery_port))
            except OSError as exc:
                print(
                    f"\033[91m[discovery] announce failed: {exc} "
                    f"(target=255.255.255.255:{discovery_port}, streamer_ip={streamer_ip})\033[0m"
                )
            stop_event.wait(interval)
    finally:
        announce_socket.close()

def generate_simulated_frame(camera_id: int, frame_count: int, width: int = 640, height: int = 640) -> np.ndarray:
    """Generate a synthetic camera frame with current time on random color background."""
    # Keep a stable deterministic color for each simulated camera.
    rng = np.random.default_rng(seed=camera_id * 1000)
    bg_color = tuple(rng.integers(50, 200, size=3).tolist())
    
    # Create solid color background
    frame = np.full((height, width, 3), bg_color, dtype=np.uint8)

    # Add a deterministic checkerboard so texture is stable over time.
    checker_size = 8
    checker_color = (
        min(255, bg_color[0] + 40),
        min(255, bg_color[1] + 40),
        min(255, bg_color[2] + 40),
    )
    for i in range(0, height, checker_size):
        for j in range(0, width, checker_size):
            if (i // checker_size + j // checker_size) % 2 == 0:
                frame[i:i + checker_size, j:j + checker_size] = checker_color
    
    _ = frame_count
    
    # Add timestamp with dark background
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    cv2.rectangle(frame, (150, 300), (490, 380), (0, 0, 0), -1)
    cv2.putText(frame, timestamp, (180, 360), 
                cv2.FONT_HERSHEY_DUPLEX, 2.0, (255, 255, 255), 3)
    
    return frame

def broadcast_camera_data(config: BroadcastConfig, stop_event: multiprocessing.Event):
    # Publish frames from a single camera on tcp://*:{port} until stop_event is set.
    context = zmq.Context()
    footage_socket = context.socket(zmq.PUB)
    footage_socket.setsockopt(zmq.CONFLATE, 1)
    footage_socket.setsockopt(zmq.LINGER, 0)
    bind_addr = f'tcp://*:{config.port}'
    print(f"\033[92m[stream-{config.port}] Binding PUB socket to {bind_addr}\033[0m")
    footage_socket.bind(bind_addr) # 172.20.10.3

    camera = None
    if not config.simulation:
        camera = cv2.VideoCapture(config.camera_id)  # init the camera
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        camera.set(cv2.CAP_PROP_FPS, config.target_fps)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        print(f"\033[92m[stream-{config.port}] Camera {config.camera_id} opened: {camera.isOpened()}\033[0m")
    else:
        print(f"\033[92m[stream-{config.port}] Simulated camera {config.camera_id} initialized\033[0m")
    
    frame_count = 0
    frame_interval = 1.0 / config.target_fps
    max_backoff_seconds = 10
    warn_every_n_failures = 25

    try:
        failed_frame_count = 0
        while not stop_event.is_set():
            frame_start = time.time()

            if config.simulation:
                # Generate simulated frame
                frame = generate_simulated_frame(config.camera_id, frame_count)
                grabbed = True
            else:
                # Read from real camera
                grabbed, frame = camera.read()
            
            frame_count += 1
            if not grabbed or frame is None:
                if failed_frame_count == 0 or failed_frame_count % warn_every_n_failures == 0:
                    print(
                        f"\033[91m[stream-{config.port}] repeated camera read failures "
                        f"(count={failed_frame_count + 1}, grabbed={grabbed})\033[0m"
                    )
                time.sleep(min(max_backoff_seconds, 0.2 * 2**failed_frame_count))
                failed_frame_count += 1
                continue

            # encode
            encoded, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, config.jpg_quality])
            if not encoded:
                if failed_frame_count == 0 or failed_frame_count % warn_every_n_failures == 0:
                    print(
                        f"\033[91m[stream-{config.port}] repeated encoding failures "
                        f"(count={failed_frame_count + 1})\033[0m"
                    )
                time.sleep(min(max_backoff_seconds, 0.2 * 2**failed_frame_count))
                failed_frame_count += 1
                continue

            failed_frame_count = 0  # reset on success
            jpg_as_text = base64.b64encode(buffer)

            try:
                footage_socket.send(jpg_as_text)
            except zmq.ZMQError as e:
                print(f"\033[91m[stream-{config.port}] ZMQ send error: {e}\033[0m")
                break

            frame_end = time.time()
            elapsed = frame_end - frame_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            
    finally:
        print(f"[stream-{config.port}] cleaning up camera and socket (frames sent: {frame_count})")
        if camera is not None:
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

def start_multiple_streams(base_port: int, camera_ids: List[int], jpg_quality: int, target_fps: int, simulation: bool = False):
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
        broadcast_config = BroadcastConfig(
            port=port, 
            camera_id=cam_id, 
            jpg_quality=jpg_quality, 
            target_fps=target_fps,
            simulation=simulation
        )
        p = multiprocessing.Process(
            target=broadcast_camera_data, 
            args=(broadcast_config, stop_event), 
            daemon=True
        )
        p.start()
        processes.append(p)
        print(f"\033[93mAttempting to start camera {cam_id} on port {port}\033[0m")

        time.sleep(1)  # slight delay to stagger startups

    return stop_event, processes

def find_available_cameras() -> List[int]:
    available_cameras = []
    for cam_id in range(0, 8, 2):  # check first 8 camera IDs (0-7)
        if f"video{cam_id}" not in os.listdir('/dev'):
            continue
        cap = cv2.VideoCapture(cam_id)
        if cap.isOpened():
            available_cameras.append(cam_id)
            cap.release()
    return available_cameras

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='camera_streamer', description='Streams one or more cameras using OpenCV over ZMQ')
    parser.add_argument('--base-port', type=_int_in_range('base-port', 1, 65535), help='Starting port for the first camera. Subsequent cameras use base-port+index')
    parser.add_argument('--camera-ids', type=int, nargs='+', help='List of camera IDs to stream (example: --camera-ids 0 2 4)')
    parser.add_argument('--auto-find-cameras', type=str, help='Automatically find available camera IDs (on or off, overrides --camera-ids)', choices=['on', 'off'])
    parser.add_argument('--jpg-quality', type=_int_in_range('jpg-quality', 1, 100), help='Quality of jpgs being transmitted (1-100)')
    parser.add_argument('--target-fps', type=_int_in_range('target-fps', 1), help='Target frames per second for streaming')
    parser.add_argument('--simulate-cameras', type=_int_in_range('simulate-cameras', 1), help='Simulate N cameras instead of real cameras (for testing)')
    parser.add_argument('--streamer-name', type=str, help='Name announced during discovery (used by receiver filter)')
    parser.add_argument('--announce-discovery', type=str, choices=['on', 'off'], help='Broadcast stream metadata for receiver auto-configuration')
    parser.add_argument('--discovery-port', type=_int_in_range('discovery-port', 1, 65535), help='UDP port used for discovery announcements')
    parser.add_argument('--discovery-interval', type=float, help='Seconds between discovery announcements')

    try:
        apply_required_external_defaults(parser, "streamer-only")
    except RuntimeError as exc:
        print(f"\033[91m[defaults] {exc}\033[0m")
        exit(2)

    args = parser.parse_args()

    base_port = args.base_port
    camera_ids = args.camera_ids
    jpg_quality = args.jpg_quality
    target_fps = args.target_fps
    simulate_cameras = args.simulate_cameras
    streamer_name = args.streamer_name
    announce_discovery = args.announce_discovery.lower() == 'on'
    discovery_port = args.discovery_port
    discovery_interval = args.discovery_interval

    if simulate_cameras is not None:
        camera_ids = list(range(simulate_cameras))
    elif args.auto_find_cameras.lower() == 'on':
        camera_ids = find_available_cameras()

    if any(camera_id < 0 for camera_id in camera_ids):
        print("\033[91mcamera-ids must only contain non-negative integers\033[0m")
        exit(2)

    max_port = base_port + len(camera_ids) - 1
    if max_port > 65535:
        print(
            f"\033[91mInvalid port range: base-port={base_port} with {len(camera_ids)} streams "
            f"would exceed 65535 (max={max_port}).\033[0m"
        )
        exit(2)

    if not camera_ids:
        print("\033[91mNo available cameras found. Exiting.\033[0m")
        exit(1)

    stop_event, processes = start_multiple_streams(base_port, camera_ids, jpg_quality, target_fps, simulation=simulate_cameras is not None)

    discovery_thread = None
    if announce_discovery:
        streamer_ip = resolve_local_ip()
        discovery_thread = threading.Thread(
            target=announce_stream_config,
            args=(
                stop_event,
                streamer_name,
                streamer_ip,
                base_port,
                camera_ids,
                discovery_port,
                discovery_interval,
            ),
            daemon=True,
        )
        discovery_thread.start()

    install_stop_signal_handlers(stop_event.set, "Stopping streams...")

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        stop_event.set()
        for p in processes:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()

    if discovery_thread is not None:
        discovery_thread.join(timeout=1)

    print('All streams stopped')