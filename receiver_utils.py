import argparse
from common_utils import (
	apply_required_external_defaults,
	get_logger,
)
import threading
from typing import List, Optional
import numpy as np
import cv2
import time

logger = get_logger(__name__)

GSTREAMER_CONNECTION_POLL_INTERVAL_SECONDS = 0.1

def handle_arguments():
	parser = argparse.ArgumentParser(
		prog="camera_receiver",
		description="Receive one or more camera streams using GStreamer Multicast",
	)
	parser.add_argument("--broadcast-ip", type=str, help="Legacy IP argument (ignored for multicast)")
	parser.add_argument("--multicast-ip", type=str, help="UDP Multicast IP group for receiving (e.g. 224.1.1.1)")
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
	def __init__(self, multicast_ip: str, port: int, timeout: float, stop_event: threading.Event, frame_store: FrameStore, window_prefix: str):
		self.multicast_ip = multicast_ip
		self.port = port
		self.timeout = timeout
		self.stop_event = stop_event
		self.frame_store = frame_store
		self.window_prefix = window_prefix

	def start(self):
		pipeline = (
			f"udpsrc multicast-group={self.multicast_ip} port={self.port} auto-multicast=true ! "
			"application/x-rtp,media=video,clock-rate=90000,payload=96,encoding-name=H264 ! "
			"rtpjitterbuffer latency=0 ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false"
		)
		window_name = f"{self.window_prefix}-{self.port}"
		logger.info(f"[{window_name}] Attempting to connect to multicast {self.multicast_ip}:{self.port}...")
		logger.info(f"[{window_name}] Pipeline: {pipeline}")

		cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

		connection_timeout = self.timeout
		start_time = time.time()
		first_frame_received = False
		decode_failures = 0
		frame_store_failures = 0

		while not self.stop_event.is_set() and not first_frame_received:
			if cap.isOpened():
				ret, _ = cap.read()
				if ret:
					first_frame_received = True
					logger.info(f"Connected to {self.multicast_ip}:{self.port} -> window '{window_name}'")
					break
			if time.time() - start_time > connection_timeout:
				logger.error(f"Failed to connect to {self.multicast_ip}:{self.port} (timeout after {connection_timeout}s)")
				return
			time.sleep(GSTREAMER_CONNECTION_POLL_INTERVAL_SECONDS)

		if not first_frame_received:
			return

		try:
			while not self.stop_event.is_set():
				ret, source = cap.read()
				if not ret or source is None:
					decode_failures += 1
					logger.error(f"[{window_name}] frame decode returned None (failure #{decode_failures})")
					time.sleep(0.01)
					continue
				
				frame_store_error = self.frame_store.set_latest(window_name, source)
				if frame_store_error is not None:
					frame_store_failures += 1
					logger.error(f"[{window_name}] frame store failed (failure #{frame_store_failures}): {frame_store_error}")
		finally:
			self.frame_store.remove_stream(window_name)
			cap.release()

class MultiReceiver:
	def __init__(self, multicast_ip: str, ports: List[int], timeout: float, window_prefix: str):
		self.multicast_ip = multicast_ip
		self.ports = ports
		self.timeout = timeout
		self.window_prefix = window_prefix

		self.stop_event = threading.Event()
		self.threads: List[threading.Thread] = []
		self.frame_store = FrameStore()

		self.sub_receivers: List[SingleReceiver] = []

	def start(self):
		for port in self.ports:
			sub_receiver = SingleReceiver(self.multicast_ip, port, self.timeout, self.stop_event, self.frame_store, self.window_prefix)
			t = threading.Thread(target=sub_receiver.start, daemon=True)
			t.start()
			self.threads.append(t)
			self.sub_receivers.append(sub_receiver)

	def stop(self):
		self.stop_event.set()
		for t in self.threads:
			t.join(timeout=5.0)

	def get_frame(self, stream_name: str):
		return self.frame_store.get_frame(stream_name)

	def get_stream_names(self) -> List[str]:
		return self.frame_store.snapshot_keys()