import argparse
from common_utils import (
	apply_required_external_defaults,
	get_logger,
)
import threading
from typing import List, Optional
import numpy as np
import zmq
import cv2
import time

logger = get_logger(__name__)

ZMQ_RECEIVE_TIMEOUT_MS = 500
ZMQ_CONNECTION_POLL_INTERVAL_SECONDS = 0.1

def handle_arguments():
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
		logger.error(f"[defaults] {exc}")
		exit(2)

	return parser.parse_args()

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

class SingleReceiver:
	def __init__(self, ip: str, port: int, timeout: float, stop_event: threading.Event, frame_store: FrameStore, window_prefix: str):
		self.ip = ip
		self.port = port
		self.timeout = timeout
		self.stop_event = stop_event
		self.frame_store = frame_store
		self.window_prefix = window_prefix

	def start(self):
		context = zmq.Context()
		footage_socket = context.socket(zmq.SUB)
		footage_socket.setsockopt(zmq.CONFLATE, 1)
		footage_socket.setsockopt(zmq.LINGER, 0)
		footage_socket.connect(f"tcp://{self.ip}:{self.port}")
		footage_socket.setsockopt_string(zmq.SUBSCRIBE, "")
		# set a receive timeout so we can check stop_event periodically
		footage_socket.setsockopt(zmq.RCVTIMEO, ZMQ_RECEIVE_TIMEOUT_MS)

		window_name = f"{self.window_prefix}-{self.port}"
		logger.info(f"Attempting to connect to {self.ip}:{self.port}...")

		# Verify connection by waiting for first frame (10 second timeout)
		connection_timeout = self.timeout
		start_time = time.time()
		first_frame_received = False
		decode_failures = 0
		frame_store_failures = 0

		while not self.stop_event.is_set() and not first_frame_received:
			try:
				footage_socket.recv(zmq.NOBLOCK)
				first_frame_received = True
				logger.info(
					f"Connected to {self.ip}:{self.port} -> window '{window_name}'"
				)
			except zmq.Again:
				if time.time() - start_time > connection_timeout:
					logger.error(
						f"Failed to connect to {self.ip}:{self.port} (timeout after {connection_timeout}s)"
					)
					return
				time.sleep(ZMQ_CONNECTION_POLL_INTERVAL_SECONDS)
				continue

		if not first_frame_received:
			return

		try:
			while not self.stop_event.is_set():
				try:
					frame_bytes = footage_socket.recv()
				except zmq.Again:
					continue

				try:
					npimg = np.frombuffer(frame_bytes, dtype=np.uint8)
					source = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
					if source is None:
						decode_failures += 1
						logger.error(
							f"[{window_name}] frame decode returned None (failure #{decode_failures})"
						)
						continue
					# store the latest frame for the main thread to display
					frame_store_error = self.frame_store.set_latest(window_name, source)
					if frame_store_error is not None:
						frame_store_failures += 1
						logger.error(
							f"[{window_name}] frame store failed (failure #{frame_store_failures}): {frame_store_error}"
						)
						# if storing fails, skip this frame
						continue
				except ValueError as exc:
					decode_failures += 1
					logger.error(
						f"[{window_name}] malformed frame payload: {exc}"
					)
					# skip malformed frames
					continue
		finally:
			# remove any stored frame for this window
			self.frame_store.remove_stream(window_name)
			footage_socket.close()
			try:
				context.term()
			except Exception:
				pass

class MultiReceiver:
	def __init__(self, ip: str, ports: List[int], timeout: float, window_prefix: str):
		self.ip = ip
		self.ports = ports
		self.timeout = timeout
		self.window_prefix = window_prefix

		self.stop_event = threading.Event()
		self.threads: List[threading.Thread] = []
		self.frame_store = FrameStore()

		self.sub_receivers: List[SingleReceiver] = []

	def start(self):
		for port in self.ports:
			sub_receiver = SingleReceiver(self.ip, port, self.timeout, self.stop_event, self.frame_store, self.window_prefix)
			t = threading.Thread(target=sub_receiver.start, daemon=True)
			t.start()
			self.threads.append(t)
			self.sub_receivers.append(sub_receiver)

	def stop(self):
		self.stop_event.set()
		for t in self.threads:
			t.join()

	def get_frame(self, stream_name: str):
		return self.frame_store.get_frame(stream_name)

	def get_stream_names(self) -> List[str]:
		return self.frame_store.snapshot_keys()