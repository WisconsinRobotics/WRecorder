import ipaddress
import cv2
import numpy as np
import time
from common_utils import (
	int_in_range,
	apply_required_external_defaults,
	VALID_PORT_MIN,
	VALID_PORT_MAX,
	get_logger,
	clamp,
)
import zmq
import argparse
import subprocess
import multiprocessing
from typing import List

logger = get_logger(__name__)

CAMERA_FRAME_WIDTH = 640
CAMERA_FRAME_HEIGHT = 640

CAMERA_BACKOFF_BASE_SECONDS = 0.2
CAMERA_MAX_BACKOFF_SECONDS = 10
STARTUP_STAGGER_SECONDS = 1


def resolve_local_ip() -> str:
	result = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
	if result.stdout:
		try:
			# Return the first non-loopback IP address
			for ip_str in result.stdout.strip().split():
				ip = ipaddress.ip_address(ip_str)
				if not ip.is_loopback:
					return ip_str
		except Exception as e:
			logger.error(f"Error parsing local IP addresses: {e}")

	return "127.0.0.1"


class StreamerConfig:
	def __init__(
		self,
		port: int,
		camera_id: int,
		jpg_quality: int,
		target_fps: int,
		simulation: bool = False,
	):
		self.port = port
		self.camera_id = camera_id
		self.jpg_quality = jpg_quality
		self.target_fps = target_fps
		self.simulation = simulation


class CameraHandler:
	def __init__(self, config: StreamerConfig):
		self.config = config
		self.camera = None

		# Initialize camera resources here (e.g., open video capture)
		if not self.config.simulation:
			self._configure_real_camera()
		else:
			logger.info(
				f"[stream-{self.config.port}] Simulated camera {self.config.camera_id} initialized"
			)

	def _configure_real_camera(self):
		self.camera = cv2.VideoCapture(self.config.camera_id)  # init the camera
		self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_FRAME_WIDTH)
		self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_FRAME_HEIGHT)
		self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
		self.camera.set(cv2.CAP_PROP_FPS, self.config.target_fps)
		self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

		logger.info(
			f"[stream-{self.config.port}] Camera {self.config.camera_id} opened: {self.camera.isOpened()}"
		)

	def read_frame(self) -> tuple[bool, np.ndarray]:
		if self.config.simulation:
			return True, self._generate_simulated_frame(camera_id=self.config.camera_id)
		else:
			return self._read_real_frame()

	def get_encoded_frame(self) -> tuple[bool, bytes]:
		grabbed, frame = self.read_frame()
		if not grabbed or frame is None:
			return False, b""
		encoded, buffer = cv2.imencode(
			".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.config.jpg_quality]
		)
		if not encoded:
			return False, b""
		return True, buffer.tobytes()

	def _generate_simulated_frame(
		self, camera_id: int, width: int = 640, height: int = 640
	) -> np.ndarray:
		"""Generate a synthetic camera frame with current time on random color background."""

		# Keep a stable deterministic color for each simulated camera.
		rng = np.random.default_rng(seed=camera_id * 1007)
		bg_color = tuple(rng.integers(0, 200, size=3).tolist())

		# Create solid color background
		frame = np.full((height, width, 3), bg_color, dtype=np.uint8)

		# Add a deterministic checkerboard so texture is stable over time.
		checker_size = 8
		checker_color = (
			min(255, bg_color[0] + 55),
			min(255, bg_color[1] + 55),
			min(255, bg_color[2] + 55),
		)
		for i in range(0, height, checker_size):
			for j in range(0, width, checker_size):
				if (i // checker_size + j // checker_size) % 2 == 0:
					frame[i : i + checker_size, j : j + checker_size] = checker_color

		TIMESTAMP_BOX_TOP_LEFT = (150, 300)
		TIMESTAMP_BOX_BOTTOM_RIGHT = (490, 380)
		TIMESTAMP_TEXT_ORIGIN = (180, 360)
		TIMESTAMP_FONT_SCALE = 2.0
		TIMESTAMP_THICKNESS = 3

		# Add timestamp with dark background
		timestamp = time.strftime("%H:%M:%S", time.localtime())
		cv2.rectangle(
			frame, TIMESTAMP_BOX_TOP_LEFT, TIMESTAMP_BOX_BOTTOM_RIGHT, (0, 0, 0), -1
		)
		cv2.putText(
			frame,
			timestamp,
			TIMESTAMP_TEXT_ORIGIN,
			cv2.FONT_HERSHEY_DUPLEX,
			TIMESTAMP_FONT_SCALE,
			(255, 255, 255),
			TIMESTAMP_THICKNESS,
		)

		return frame

	def _read_real_frame(self) -> tuple[bool, np.ndarray]:
		return self.camera.read()

	def release(self):
		if self.camera is not None:
			self.camera.release()


class ZMQHandler:
	def __init__(self, config: StreamerConfig):
		self.config = config
		self.context = zmq.Context()
		self.footage_socket = self.context.socket(zmq.PUB)
		self.footage_socket.setsockopt(zmq.CONFLATE, 1)
		self.footage_socket.setsockopt(zmq.LINGER, 0)
		bind_addr = f"tcp://*:{config.port}"
		logger.info(f"[stream-{config.port}] Binding PUB socket to {bind_addr}")
		self.footage_socket.bind(bind_addr)

	def send_frame(self, encoded_frame: bytes) -> bool:
		try:
			self.footage_socket.send(encoded_frame)
			return True
		except zmq.ZMQError as e:
			logger.error(f"[stream-{self.config.port}] ZMQ send error: {e}")
			return False

	def close(self):
		try:
			self.footage_socket.close()
		except Exception as e:
			logger.error(f"[stream-{self.config.port}] error closing socket: {e}")
		try:
			self.context.term()
		except Exception as e:
			logger.error(f"[stream-{self.config.port}] error terminating context: {e}")


def handle_arguments():
	parser = argparse.ArgumentParser(
		prog="camera_streamer",
		description="Streams one or more cameras using OpenCV over ZMQ",
	)
	parser.add_argument(
		"--base-port",
		type=int_in_range("base-port", VALID_PORT_MIN, VALID_PORT_MAX),
		help="Starting port for the first camera. Subsequent cameras use base-port+index",
	)
	parser.add_argument(
		"--camera-ids",
		type=int,
		nargs="+",
		help="List of camera IDs to stream (example: --camera-ids 0 2 4)",
	)
	parser.add_argument(
		"--auto-find-cameras",
		type=str,
		help="Automatically find available camera IDs (on or off, overrides --camera-ids)",
		choices=["on", "off"],
	)
	parser.add_argument(
		"--jpg-quality",
		type=int_in_range("jpg-quality", 1, 100),
		help="Quality of jpgs being transmitted (1-100)",
	)
	parser.add_argument(
		"--target-fps",
		type=int_in_range("target-fps", 1),
		help="Target frames per second for streaming",
	)
	parser.add_argument(
		"--simulate-cameras",
		type=int_in_range("simulate-cameras", 1),
		help="Simulate N cameras instead of real cameras (for testing)",
	)
	parser.add_argument(
		"--streamer-name",
		type=str,
		help="Name announced during discovery (used by receiver filter)",
	)
	parser.add_argument(
		"--announce-discovery",
		type=str,
		choices=["on", "off"],
		help="Broadcast stream metadata for receiver auto-configuration",
	)
	parser.add_argument(
		"--discovery-port",
		type=int_in_range("discovery-port", VALID_PORT_MIN, VALID_PORT_MAX),
		help="UDP port used for discovery announcements",
	)
	parser.add_argument(
		"--discovery-interval",
		type=float,
		help="Seconds between discovery announcements",
	)

	try:
		apply_required_external_defaults(parser, "streamer-only")
	except RuntimeError as exc:
		logger.error(f"[defaults] {exc}")
		exit(2)

	return parser.parse_args()


class SingleStreamer:
	def __init__(self, config: StreamerConfig):
		self.config = config

	def start(self, stop_event: multiprocessing.Event):
		camera_handler = CameraHandler(self.config)
		zmq_handler = ZMQHandler(self.config)

		frame_count = 0
		frame_interval = 1.0 / self.config.target_fps

		try:
			failed_frame_count = 0
			while not stop_event.is_set():
				frame_start = time.time()

				success, encoded_frame = camera_handler.get_encoded_frame()
				if not success or encoded_frame is None:
					logger.error(
						f"[stream-{self.config.port}] failed to get encoded frame (failure #{failed_frame_count + 1})"
					)
					# sleep 2^CAMERA_BACKOFF_BASE_SECONDS up to CAMERA_MAX_BACKOFF_SECONDS between failures to avoid tight failure loops
					time.sleep(
						clamp(
							CAMERA_BACKOFF_BASE_SECONDS * (2**failed_frame_count),
							0,
							CAMERA_MAX_BACKOFF_SECONDS,
						)
					)
					failed_frame_count += 1
					continue
				failed_frame_count = 0  # reset on success

				success = zmq_handler.send_frame(encoded_frame)
				if not success:
					logger.error(f"[stream-{self.config.port}] failed to send frame")
					continue
				frame_count += 1

				frame_end = time.time()
				elapsed = frame_end - frame_start
				sleep_time = frame_interval - elapsed
				if sleep_time > 0:
					time.sleep(sleep_time)

		finally:
			logger.info(
				f"[stream-{self.config.port}] cleaning up camera and socket (frames sent: {frame_count})"
			)

			camera_handler.release()
			zmq_handler.close()


class MultiStreamer:
	def __init__(
		self,
		base_port: int,
		camera_ids: List[int],
		jpg_quality: int,
		target_fps: int,
		simulation: bool = False,
	):
		self.streamers = [
			SingleStreamer(
				StreamerConfig(
					port=base_port + idx,
					camera_id=cam_id,
					jpg_quality=jpg_quality,
					target_fps=target_fps,
					simulation=simulation,
				)
			)
			for idx, cam_id in enumerate(camera_ids)
		]

		self.stop_event = multiprocessing.Event()
		self.processes: List[multiprocessing.Process] = []

	def start(self):
		for sub_streamer in self.streamers:
			p = multiprocessing.Process(target=sub_streamer.start, args=(self.stop_event,), daemon=True)
			p.start()
			self.processes.append(p)

			logger.info(
				f"Attempting to start camera {sub_streamer.config.camera_id} on port {sub_streamer.config.port}"
			)

			time.sleep(STARTUP_STAGGER_SECONDS)  # stagger camera startups to reduce contention
