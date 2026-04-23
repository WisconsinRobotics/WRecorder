import cv2
import zmq
import numpy as np
import socket
import argparse
import threading
from typing import List, Optional
import time
from common_utils import (
	ANSI_GREEN,
	ANSI_RED,
	ANSI_YELLOW,
	apply_required_external_defaults,
	build_sequential_ports,
	color_text,
	install_stop_signal_handlers,
	parse_discovery_payload,
	rate_limited_warn,
)


ZMQ_RECEIVE_TIMEOUT_MS = 500
ZMQ_CONNECTION_POLL_INTERVAL_SECONDS = 0.1
DISCOVERY_SOCKET_TIMEOUT_SECONDS = 0.25
DISCOVERY_BUFFER_SIZE_BYTES = 4096
MIN_TIMEOUT_SECONDS = 0.1
DISCOVERY_TIMEOUT_CAP_SECONDS = 5.0
DISCOVERY_TIMEOUT_RATIO = 0.5
WARN_EVERY_N_FAILURES = 50
DISPLAY_WAIT_KEY_MS = 30


class FrameStore:
	def __init__(self):
		self._frames: dict = {}
		self._lock = threading.Lock()

	def set_latest(self, stream_name: str, frame: np.ndarray) -> Optional[Exception]:
		try:
			with self._lock:
				self._frames[stream_name] = frame.copy()
			return None
		except (TypeError, RuntimeError) as exc:
			return exc

	def remove_stream(self, stream_name: str):
		try:
			with self._lock:
				self._frames.pop(stream_name, None)
		except Exception:
			pass

	def snapshot_keys(self) -> List[str]:
		with self._lock:
			return list(self._frames.keys())

	def get_frame(self, stream_name: str):
		with self._lock:
			return self._frames.get(stream_name)


def calculate_discovery_budget(
	total_timeout: float, discovery_timeout_override: Optional[float]
) -> float:
	if discovery_timeout_override is None:
		discovery_budget = min(
			DISCOVERY_TIMEOUT_CAP_SECONDS, total_timeout * DISCOVERY_TIMEOUT_RATIO
		)
	else:
		discovery_budget = discovery_timeout_override
	return max(MIN_TIMEOUT_SECONDS, min(discovery_budget, total_timeout))


def calculate_remaining_connection_timeout(
	total_timeout: float, setup_start_time: float
) -> float:
	elapsed = time.time() - setup_start_time
	return max(MIN_TIMEOUT_SECONDS, total_timeout - elapsed)


def receive_camera_data(
	ip: str,
	port: int,
	timeout: float,
	stop_event: threading.Event,
	frame_store: FrameStore,
	window_prefix: str = "Stream",
):
	"""Subscribe to a single publisher at ip:port and display frames until stop_event is set."""
	context = zmq.Context()
	footage_socket = context.socket(zmq.SUB)
	footage_socket.setsockopt(zmq.CONFLATE, 1)
	footage_socket.setsockopt(zmq.LINGER, 0)
	footage_socket.connect(f"tcp://{ip}:{port}")
	footage_socket.setsockopt_string(zmq.SUBSCRIBE, "")
	# set a receive timeout so we can check stop_event periodically
	footage_socket.setsockopt(zmq.RCVTIMEO, ZMQ_RECEIVE_TIMEOUT_MS)

	window_name = f"{window_prefix}-{port}"
	print(color_text(f"Attempting to connect to {ip}:{port}...", ANSI_YELLOW))

	# Verify connection by waiting for first frame (10 second timeout)
	connection_timeout = timeout
	start_time = time.time()
	first_frame_received = False
	decode_failures = 0
	frame_store_failures = 0

	while not stop_event.is_set() and not first_frame_received:
		try:
			footage_socket.recv(zmq.NOBLOCK)
			first_frame_received = True
			print(
				color_text(
					f"Connected to {ip}:{port} -> window '{window_name}'", ANSI_GREEN
				)
			)
			print(color_text("Press 'q' in any window to quit", ANSI_GREEN))
		except zmq.Again:
			if time.time() - start_time > connection_timeout:
				print(
					color_text(
						f"Failed to connect to {ip}:{port} (timeout after {connection_timeout}s)",
						ANSI_RED,
					)
				)
				return
			time.sleep(ZMQ_CONNECTION_POLL_INTERVAL_SECONDS)
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
				npimg = np.frombuffer(frame_bytes, dtype=np.uint8)
				source = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
				if source is None:
					decode_failures += 1
					rate_limited_warn(
						decode_failures,
						f"[{window_name}] frame decode returned None"
					)
					continue
				# store the latest frame for the main thread to display
				frame_store_error = frame_store.set_latest(window_name, source)
				if frame_store_error is not None:
					frame_store_failures += 1
					rate_limited_warn(
						frame_store_failures,
						f"[{window_name}] frame store failed: {frame_store_error}"
					)
					# if storing fails, skip this frame
					continue
			except ValueError as exc:
				decode_failures += 1
				rate_limited_warn(
					decode_failures,
					f"[{window_name}] malformed frame payload: {exc}"
				)
				# skip malformed frames
				continue
	finally:
		# remove any stored frame for this window
		frame_store.remove_stream(window_name)
		footage_socket.close()
		try:
			context.term()
		except Exception:
			pass


def discover_stream_config(
	discovery_port: int, timeout: float, streamer_name_filter: str = None
):
	"""Listen for UDP discovery heartbeats and return the first matching stream config."""
	receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	receiver_socket.bind(("", discovery_port))
	receiver_socket.settimeout(DISCOVERY_SOCKET_TIMEOUT_SECONDS)

	print(
		color_text(
			f"Listening for discovery on UDP {discovery_port} for up to {timeout:.1f}s"
			f"{f' (filter={streamer_name_filter})' if streamer_name_filter else ''}...",
			ANSI_YELLOW,
		)
	)

	deadline = time.time() + max(MIN_TIMEOUT_SECONDS, timeout)
	try:
		while time.time() < deadline:
			try:
				data, _addr = receiver_socket.recvfrom(DISCOVERY_BUFFER_SIZE_BYTES)
			except socket.timeout:
				continue

			discovered = parse_discovery_payload(data, streamer_name_filter)
			if discovered is None:
				continue

			print(
				color_text(
					f"Discovered '{discovered['streamer_name']}' at {discovered['streamer_ip']} "
					f"(base_port={discovered['base_port']}, streams={discovered['stream_count']})",
					ANSI_GREEN,
				)
			)
			return discovered
	finally:
		receiver_socket.close()

	return None


def start_multiple_receivers(
	ip: str, ports: List[int], timeout: float, window_prefix: str = "Stream"
):
	stop_event = threading.Event()
	threads: List[threading.Thread] = []
	frame_store = FrameStore()

	for port in ports:
		t = threading.Thread(
			target=receive_camera_data,
			args=(ip, port, timeout, stop_event, frame_store, window_prefix),
			daemon=True,
		)
		t.start()
		threads.append(t)

	return stop_event, threads, frame_store


if __name__ == "__main__":
	parser = argparse.ArgumentParser(
		prog="camera_receiver",
		description="Receive one or more camera streams over ZMQ",
	)
	parser.add_argument("--broadcast-ip", type=str, help="IP of the publisher(s)")
	parser.add_argument(
		"--base-port",
		type=int,
		help="Starting port for the first camera. Subsequent cameras use base-port+index",
	)
	parser.add_argument(
		"--count",
		type=int,
		help="Number of sequential ports to subscribe to starting at base-port",
	)
	parser.add_argument(
		"--timeout",
		type=float,
		help="Total setup timeout in seconds (discovery + initial connection)",
	)
	parser.add_argument(
		"--auto-config",
		type=str,
		choices=["on", "off"],
		help="Auto-configure from discovery announcements",
	)
	parser.add_argument(
		"--streamer-name-filter",
		type=str,
		help="Only accept discovery packets from this streamer name",
	)
	parser.add_argument(
		"--discovery-port", type=int, help="UDP port used for discovery announcements"
	)
	parser.add_argument(
		"--discovery-timeout",
		type=float,
		help="Optional override for discovery phase timeout in seconds",
	)

	try:
		apply_required_external_defaults(parser, "receiver-only")
	except RuntimeError as exc:
		print(color_text(f"[defaults] {exc}", ANSI_RED))
		exit(2)

	args = parser.parse_args()
	broadcast_ip = args.broadcast_ip
	base_port = args.base_port
	stream_count = args.count
	timeout = args.timeout
	auto_config = args.auto_config == "on"
	streamer_name_filter = args.streamer_name_filter
	discovery_port = args.discovery_port
	discovery_timeout = args.discovery_timeout

	connection_timeout = timeout
	window_prefix = "Stream"
	setup_start = time.time()

	if auto_config:
		discovery_budget = calculate_discovery_budget(timeout, discovery_timeout)

		discovered = discover_stream_config(
			discovery_port, discovery_budget, streamer_name_filter
		)
		connection_timeout = calculate_remaining_connection_timeout(
			timeout, setup_start
		)

		if discovered is not None:
			broadcast_ip = discovered["streamer_ip"]
			base_port = discovered["base_port"]
			stream_count = discovered["stream_count"]
			window_prefix = discovered["streamer_name"]
		else:
			print(
				color_text(
					"No matching discovery packet found. Falling back to manual args.",
					ANSI_YELLOW,
				)
			)

	ports = build_sequential_ports(base_port, stream_count)
	if ports is None:
		print(
			color_text(
				f"Invalid port configuration: base_port={base_port}, count={stream_count}",
				ANSI_RED,
			)
		)
		exit(2)

	print(
		color_text(
			f"Receiver config: ip={broadcast_ip}, base_port={base_port}, "
			f"count={stream_count}, connection_timeout={connection_timeout:.1f}s",
			ANSI_YELLOW,
		)
	)

	stop_event, threads, frame_store = start_multiple_receivers(
		broadcast_ip, ports, connection_timeout, window_prefix
	)

	install_stop_signal_handlers(stop_event.set, "Stopping receivers...")

	try:
		# Main loop handles displaying frames so GUI calls happen on main thread
		while any(t.is_alive() for t in threads) and not stop_event.is_set():
			keys = frame_store.snapshot_keys()
			for k in keys:
				frame = frame_store.get_frame(k)
				if frame is None:
					continue
				cv2.imshow(k, frame)
			if cv2.waitKey(DISPLAY_WAIT_KEY_MS) & 0xFF == ord("q"):
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

	print("All receivers stopped")
