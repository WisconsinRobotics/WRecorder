import ipaddress
import time
import os
import gi
import argparse
import subprocess
import multiprocessing
from typing import List

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402
from common_utils import (  # noqa: E402
	int_in_range,
	apply_required_external_defaults,
	VALID_PORT_MIN,
	VALID_PORT_MAX,
	get_logger,
	CAMERA_FRAME_WIDTH,
	CAMERA_FRAME_HEIGHT,
)

logger = get_logger(__name__)

# Initialize GStreamer
Gst.init(None)

CAMERA_BACKOFF_BASE_SECONDS = 0.2
CAMERA_MAX_BACKOFF_SECONDS = 10
STARTUP_STAGGER_SECONDS = 1

VIDEOTEST_PATTERNS = [
	"smpte",
	"snow",
	"black",
	"white",
	"red",
	"green",
	"blue",
	"checkers-1",
	"checkers-2",
	"checkers-4",
	"checkers-8",
	"circular",
	"blink",
	"smpte75",
	"zone-plate",
	"gamut",
	"chroma-zone-plate",
	"solid-color",
	"ball",
	"smpte100",
	"bar",
	"pinwheel",
	"spokes",
	"gradient",
	"colors",
	"smpte-rp-219",
]


def resolve_local_ip() -> str:
	env_ip = os.environ.get("WRECORDER_STREAMER_IP")
	if env_ip:
		return env_ip

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

def _is_gstreamer_element_available(element_name: str) -> bool:
	try:
		result = subprocess.run(["gst-inspect-1.0", element_name], capture_output=True)
		return result.returncode == 0
	except FileNotFoundError:
		return False


def _configure_camera_v4l2(camera_id: int, fps: int, width: int, height: int) -> bool:
	"""Configure V4L2 camera properties using v4l2-ctl."""
	device = f"/dev/video{camera_id}"
	
	try:
		# Set resolution
		subprocess.run(
			["v4l2-ctl", "-d", device, "-v", f"width={width},height={height}"],
			capture_output=True,
			timeout=2.0
		)
		# Set FPS
		subprocess.run(
			["v4l2-ctl", "-d", device, "-p", str(fps)],
			capture_output=True,
			timeout=2.0
		)
		return True
	except Exception as e:
		logger.warning(f"Failed to configure camera {camera_id} with v4l2-ctl: {e}")
		return False


def _videotestsrc_props_for_camera(camera_id: int) -> str:
	pattern = VIDEOTEST_PATTERNS[camera_id % len(VIDEOTEST_PATTERNS)]
	seed = camera_id + 1
	foreground = 0xFF000000 | ((seed * 73) % 256) << 16 | ((seed * 151) % 256) << 8 | ((seed * 199) % 256)
	background = 0xFF000000 | ((seed * 41) % 256) << 16 | ((seed * 97) % 256) << 8 | ((seed * 157) % 256)
	motion = ["wavy", "sweep", "hsweep"][camera_id % 3]
	horizontal_speed = ((camera_id % 5) + 1) * 2
	animation_mode = ["frames", "wall-time", "running-time"][camera_id % 3]
	return (
		f'pattern={pattern} '
		f'foreground-color={foreground} '
		f'background-color={background} '
		f'motion={motion} '
		f'horizontal-speed={horizontal_speed} '
		f'animation-mode={animation_mode}'
	)


def _build_encoder_pipeline(source_element: str, port: int, bitrate: int, target_fps: int, multicast_ip: str, grayscale: bool) -> str:
	video_chain = [source_element, "videoconvert"]
	if grayscale:
		video_chain.extend(["videobalance saturation=0.0", "videoconvert"])
	video_chain.extend([
		"video/x-raw,format=I420",
	])

	if _is_gstreamer_element_available("v4l2h264enc"):
		logger.info(f"[stream-{port}] Using hardware encoder v4l2h264enc")
		encoder_chain = (
			f'v4l2h264enc extra-controls="encode,video_bitrate={bitrate},video_gop_size={int(target_fps)}" ! '
			"h264parse"
		)
	else:
		logger.info(f"[stream-{port}] v4l2h264enc not found, falling back to software x264enc")
		kbps = max(1, bitrate // 1000)
		encoder_chain = (
			f'x264enc tune=zerolatency bitrate={kbps} speed-preset=ultrafast key-int-max={int(target_fps)} ! '
			"h264parse"
		)

	return (
		" ! ".join(video_chain)
		+ " ! "
		+ encoder_chain
		+ f" ! rtph264pay config-interval=1 pt=96 ! udpsink host={multicast_ip} port={port} auto-multicast=true sync=false"
	)


class StreamerConfig:
	def __init__(
		self,
		port: int,
		camera_id: int,
		bitrate: int,
		target_fps: int,
		multicast_ip: str,
		simulation: bool = False,
		grayscale: bool = False,
	):
		self.port = port
		self.camera_id = camera_id
		self.bitrate = bitrate
		self.target_fps = target_fps
		self.multicast_ip = multicast_ip
		self.simulation = simulation
		self.grayscale = grayscale


class StreamPipeline:
	def __init__(self, config: StreamerConfig):
		self.config = config
		self.pipeline = None
		self.bus = None

	def _build_pipeline(self) -> str:
		if self.config.simulation:
			source = (
				"videotestsrc is-live=true "
				+ _videotestsrc_props_for_camera(self.config.camera_id)
				+ f" ! video/x-raw,width={CAMERA_FRAME_WIDTH},height={CAMERA_FRAME_HEIGHT},framerate={self.config.target_fps}/1"
			)
		else:
			device = f"/dev/video{self.config.camera_id}"
			_configure_camera_v4l2(
				self.config.camera_id,
				self.config.target_fps,
				CAMERA_FRAME_WIDTH,
				CAMERA_FRAME_HEIGHT,
			)
			source = f"v4l2src device={device}"

		return _build_encoder_pipeline(
			source,
			self.config.port,
			self.config.bitrate,
			self.config.target_fps,
			self.config.multicast_ip,
			self.config.grayscale,
		)

	def start(self):
		pipeline_str = self._build_pipeline()
		logger.info(f"[stream-{self.config.port}] Initializing GStreamer pipeline for {self.config.multicast_ip}:{self.config.port}")
		logger.info(f"[stream-{self.config.port}] Pipeline: {pipeline_str}")

		try:
			self.pipeline = Gst.parse_launch(pipeline_str)
			self.bus = self.pipeline.get_bus()
			ret = self.pipeline.set_state(Gst.State.PLAYING)
			if ret == Gst.StateChangeReturn.FAILURE:
				raise RuntimeError("failed to set pipeline to PLAYING state")
			logger.info(f"[stream-{self.config.port}] GStreamer pipeline initialized")
		except Exception as e:
			logger.error(f"[stream-{self.config.port}] Failed to create GStreamer pipeline: {e}")
			self.stop()
			raise

	def run_until_stopped(self, stop_event: multiprocessing.Event):
		try:
			while not stop_event.is_set():
				if self.bus is not None:
					message = self.bus.timed_pop_filtered(
						100 * Gst.MSECOND,
						Gst.MessageType.ERROR | Gst.MessageType.EOS,
					)
					if message is not None:
						if message.type == Gst.MessageType.ERROR:
							err, debug = message.parse_error()
							logger.error(f"[stream-{self.config.port}] GStreamer error: {err.message}; debug={debug}")
						else:
							logger.info(f"[stream-{self.config.port}] GStreamer EOS received")
						stop_event.set()
						break
				time.sleep(0.1)
		finally:
			self.stop()

	def stop(self):
		if self.pipeline is not None:
			try:
				self.pipeline.set_state(Gst.State.NULL)
			except Exception as e:
				logger.error(f"[stream-{self.config.port}] error stopping pipeline: {e}")
			finally:
				self.pipeline = None
				self.bus = None


def handle_arguments():
	parser = argparse.ArgumentParser(
		prog="camera_streamer",
		description="Streams one or more cameras using OpenCV and GStreamer UDP Multicast",
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
		"--bitrate",
		type=int_in_range("bitrate", 1000, 100000000),
		help="Target video bitrate for hardware h.264 encoding",
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
	parser.add_argument(
		"--grayscale",
		type=str,
		choices=["on", "off"],
		help="Convert frames to grayscale to reduce bandwidth",
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
		stream_pipeline = StreamPipeline(self.config)
		stream_pipeline.start()
		logger.info(f"[stream-{self.config.port}] running GStreamer pipeline")
		stream_pipeline.run_until_stopped(stop_event)
		logger.info(f"[stream-{self.config.port}] cleaning up pipeline")


class MultiStreamer:
	def __init__(
		self,
		base_port: int,
		camera_ids: List[int],
		bitrate: int,
		target_fps: int,
		multicast_ip: str,
		simulation: bool = False,
		grayscale: bool = False,
	):
		self.streamers = [
			SingleStreamer(
				StreamerConfig(
					port=base_port + idx,
					camera_id=cam_id,
					bitrate=bitrate,
					target_fps=target_fps,
					multicast_ip=multicast_ip,
					simulation=simulation,
					grayscale=grayscale,
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
