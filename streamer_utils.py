import ipaddress
import cv2
import numpy as np
import time
from common_utils import color_text, ANSI_GREEN, ANSI_RED, int_in_range, apply_required_external_defaults, VALID_PORT_MIN, VALID_PORT_MAX
import zmq
import argparse
import subprocess


CAMERA_FRAME_WIDTH = 640
CAMERA_FRAME_HEIGHT = 640


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
			print(color_text(f"Error parsing local IP addresses: {e}", ANSI_RED))
			
	return "127.0.0.1"


class BroadcastConfig:
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


class Camera:
	def __init__(self, config: BroadcastConfig):
		self.config = config
		self.camera = None

		# Initialize camera resources here (e.g., open video capture)
		if not self.config.simulation:
			self._configure_real_camera()
		else:
			print(
				color_text(
					f"[stream-{self.config.port}] Simulated camera {self.config.camera_id} initialized",
					ANSI_GREEN,
				)
			)

	def _configure_real_camera(self):
		self.camera = cv2.VideoCapture(self.config.camera_id)  # init the camera
		self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_FRAME_WIDTH)
		self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_FRAME_HEIGHT)
		self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
		self.camera.set(cv2.CAP_PROP_FPS, self.config.target_fps)
		self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

		print(
			color_text(
				f"[stream-{self.config.port}] Camera {self.config.camera_id} opened: {self.camera.isOpened()}",
				ANSI_GREEN,
			)
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
	def __init__(self, config: BroadcastConfig):
		self.config = config
		self.context = zmq.Context()
		self.footage_socket = self.context.socket(zmq.PUB)
		self.footage_socket.setsockopt(zmq.CONFLATE, 1)
		self.footage_socket.setsockopt(zmq.LINGER, 0)
		bind_addr = f"tcp://*:{config.port}"
		print(
			color_text(
				f"[stream-{config.port}] Binding PUB socket to {bind_addr}", ANSI_GREEN
			)
		)
		self.footage_socket.bind(bind_addr)

	def send_frame(self, encoded_frame: bytes) -> bool:
		try:
			self.footage_socket.send(encoded_frame)
			return True
		except zmq.ZMQError as e:
			print(
				color_text(f"[stream-{self.config.port}] ZMQ send error: {e}", ANSI_RED)
			)
			return False
	
	def close(self):
		try:
			self.footage_socket.close()
		except Exception as e:
			print(f"[stream-{self.config.port}] error closing socket: {e}")
		try:
			self.context.term()
		except Exception as e:
			print(f"[stream-{self.config.port}] error terminating context: {e}")

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
		print(color_text(f"[defaults] {exc}", ANSI_RED))
		exit(2)

	return parser.parse_args()