import ipaddress
import time
import os
import gi
import argparse
import subprocess
import multiprocessing
import queue as queue_module
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

CAMERA_RESTART_BASE_SECONDS = 0.5
CAMERA_RESTART_MAX_SECONDS = 10
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


def _h264_level_for_frame_rate(width: int, height: int, fps: int) -> str:
	macroblocks_w = (width + 15) // 16
	macroblocks_h = (height + 15) // 16
	macroblocks_per_frame = macroblocks_w * macroblocks_h
	macroblocks_per_second = macroblocks_per_frame * fps

	level_limits = [
		("1", 99, 1485),
		("1b", 99, 1485),
		("1.1", 396, 3000),
		("1.2", 396, 6000),
		("1.3", 396, 11880),
		("2", 396, 11880),
		("2.1", 792, 19800),
		("2.2", 1620, 20250),
		("3", 1620, 40500),
		("3.1", 3600, 108000),
		("3.2", 5120, 216000),
		("4", 8192, 245760),
		("4.1", 8192, 245760),
		("4.2", 8704, 522240),
	]

	for level, max_macroblocks_per_frame, max_macroblocks_per_second in level_limits:
		if (
			macroblocks_per_frame <= max_macroblocks_per_frame
			and macroblocks_per_second <= max_macroblocks_per_second
		):
			return level

	return "4.2"


def _build_encoder_pipeline(
	source_element: str,
	port: int,
	bitrate: int,
	target_fps: int,
	multicast_ip: str,
	force_software: bool = False,
	output_width: int = CAMERA_FRAME_WIDTH,
	output_height: int = CAMERA_FRAME_HEIGHT,
) -> str:
	video_chain = [source_element, "videoconvert", "video/x-raw,format=I420"]

	use_v4l2 = (not force_software) and _is_gstreamer_element_available("v4l2h264enc")
	if use_v4l2:
		logger.info(f"[stream-{port}] Using hardware encoder v4l2h264enc")
		level = _h264_level_for_frame_rate(output_width, output_height, target_fps)
		encoder_chain = (
			f'v4l2h264enc extra-controls="encode,video_bitrate={bitrate},video_gop_size={int(target_fps)}" ! '
			f'h264parse ! video/x-h264,level=(string){level}'
		)
	else:
		if force_software:
			logger.info(f"[stream-{port}] Forcing software encoder x264enc (fallback)")
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
		force_software: bool = False,
	):
		self.port = port
		self.camera_id = camera_id
		self.bitrate = bitrate
		self.target_fps = target_fps
		self.multicast_ip = multicast_ip
		self.simulation = simulation
		self.force_software = force_software


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
			force_software=getattr(self.config, "force_software", False),
			output_width=CAMERA_FRAME_WIDTH,
			output_height=CAMERA_FRAME_HEIGHT,
		)

	def start(self) -> bool:
		pipeline_str = self._build_pipeline()
		logger.info(f"[stream-{self.config.port}] Initializing GStreamer pipeline for {self.config.multicast_ip}:{self.config.port}")
		logger.info(f"[stream-{self.config.port}] Pipeline: {pipeline_str}")

		# Try to start pipeline; if starting fails and v4l2 was used, retry with software encoder
		try:
			self.pipeline = Gst.parse_launch(pipeline_str)
			self.bus = self.pipeline.get_bus()
			ret = self.pipeline.set_state(Gst.State.PLAYING)
			if ret == Gst.StateChangeReturn.FAILURE:
				raise RuntimeError("failed to set pipeline to PLAYING state")
			logger.info(f"[stream-{self.config.port}] GStreamer pipeline initialized")
			return True
		except Exception as e:
			logger.error(f"[stream-{self.config.port}] Failed to create GStreamer pipeline: {e}")
			# If we haven't already forced software encoding, try that as a fallback
			if not getattr(self.config, "force_software", False):
				logger.info(f"[stream-{self.config.port}] Retrying with software encoder (fallback)")
				setattr(self.config, "force_software", True)
				try:
					pipeline_str = self._build_pipeline()
					self.pipeline = Gst.parse_launch(pipeline_str)
					self.bus = self.pipeline.get_bus()
					ret = self.pipeline.set_state(Gst.State.PLAYING)
					if ret == Gst.StateChangeReturn.FAILURE:
						raise RuntimeError("failed to set pipeline to PLAYING state (software)")
					logger.info(f"[stream-{self.config.port}] GStreamer pipeline initialized (software encoder)")
					return True
				except Exception as e2:
					logger.error(f"[stream-{self.config.port}] Fallback to software encoder failed: {e2}")
			# give up
			self.stop()
			return False

	def run_until_stopped(self, stop_event: multiprocessing.Event) -> bool:
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
							# If this looks like a v4l2 encoder frame handling failure, try runtime fallback to software encoder
							err_text = (err.message or "").lower()
							debug_text = (debug or "").lower()
							if ("gst_v4l2_video_enc_handle_frame" in debug_text or "v4l2" in err_text or "v4l2" in debug_text) and not getattr(self.config, "force_software", False):
								logger.info(f"[stream-{self.config.port}] Detected v4l2 encoder failure; attempting runtime fallback to software encoder")
								# Attempt to restart with software encoder
								self.stop()
								setattr(self.config, "force_software", True)
								try:
									pipeline_str = self._build_pipeline()
									logger.info(f"[stream-{self.config.port}] Restarting pipeline with software encoder: {pipeline_str}")
									self.pipeline = Gst.parse_launch(pipeline_str)
									self.bus = self.pipeline.get_bus()
									ret = self.pipeline.set_state(Gst.State.PLAYING)
									if ret == Gst.StateChangeReturn.FAILURE:
										raise RuntimeError("failed to set pipeline to PLAYING state (runtime software)")
									logger.info(f"[stream-{self.config.port}] Pipeline restarted with software encoder")
								except Exception as e3:
									logger.error(f"[stream-{self.config.port}] Runtime fallback failed: {e3}")
									break
							else:
								logger.info(f"[stream-{self.config.port}] GStreamer EOS received")
								break
				time.sleep(0.1)
			return stop_event.is_set()
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


def _publish_stream_status(
	status_queue: multiprocessing.Queue,
	port: int,
	state: str,
	reason: str,
):
	try:
		status_queue.put_nowait(
			{
				"port": port,
				"state": state,
				"reason": reason,
			}
		)
	except Exception:
		pass


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
		"--never-give-up",
		type=str,
		choices=["on", "off"],
		help="Keep restarting cameras even if all streams have failed",
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

	def start(self, stop_event: multiprocessing.Event, status_queue: multiprocessing.Queue):
		restart_count = 0
		while not stop_event.is_set():
			stream_pipeline = StreamPipeline(self.config)
			if not stream_pipeline.start():
				_publish_stream_status(
					status_queue,
					self.config.port,
					"failed",
					"pipeline_start_failed",
				)
				restart_count += 1
				delay = min(CAMERA_RESTART_BASE_SECONDS * (2 ** (restart_count - 1)), CAMERA_RESTART_MAX_SECONDS)
				logger.warning(
					f"[stream-{self.config.port}] pipeline failed to start; retrying in {delay:.1f}s (attempt #{restart_count})"
				)
				time.sleep(delay)
				continue

			_publish_stream_status(status_queue, self.config.port, "healthy", "pipeline_started")

			logger.info(f"[stream-{self.config.port}] running GStreamer pipeline")
			stopped_by_request = stream_pipeline.run_until_stopped(stop_event)
			logger.info(f"[stream-{self.config.port}] cleaning up pipeline")
			_publish_stream_status(
				status_queue,
				self.config.port,
				"failed" if not stop_event.is_set() else "starting",
				"pipeline_stopped" if not stop_event.is_set() else "shutdown_requested",
			)

			if stopped_by_request or stop_event.is_set():
				break

			restart_count += 1
			delay = min(CAMERA_RESTART_BASE_SECONDS * (2 ** (restart_count - 1)), CAMERA_RESTART_MAX_SECONDS)
			logger.warning(
				f"[stream-{self.config.port}] pipeline stopped unexpectedly; restarting in {delay:.1f}s (attempt #{restart_count})"
			)
			time.sleep(delay)


def _spawn_streamer_process(
	streamer: "SingleStreamer",
	stop_event: multiprocessing.Event,
	status_queue: multiprocessing.Queue,
) -> multiprocessing.Process:
	p = multiprocessing.Process(target=streamer.start, args=(stop_event, status_queue), daemon=True)
	p.start()
	return p


class MultiStreamer:
	def __init__(
		self,
		base_port: int,
		camera_ids: List[int],
		bitrate: int,
		target_fps: int,
		multicast_ip: str,
		simulation: bool = False,
		never_give_up: bool = False,
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
				)
			)
			for idx, cam_id in enumerate(camera_ids)
		]

		self.stop_event = multiprocessing.Event()
		self.never_give_up = never_give_up
		self.status_queue: multiprocessing.Queue = multiprocessing.Queue()
		self.stream_health = {sub_streamer.config.port: "starting" for sub_streamer in self.streamers}
		self.processes: List[multiprocessing.Process] = []

	def start(self):
		for sub_streamer in self.streamers:
			p = _spawn_streamer_process(sub_streamer, self.stop_event, self.status_queue)
			self.processes.append(p)

			logger.info(
				f"Attempting to start camera {sub_streamer.config.camera_id} on port {sub_streamer.config.port}"
			)

			time.sleep(STARTUP_STAGGER_SECONDS)  # stagger camera startups to reduce contention

	def supervise(self):
		while not self.stop_event.is_set():
			while True:
				try:
					status = self.status_queue.get_nowait()
				except queue_module.Empty:
					break
				port = status.get("port")
				if port in self.stream_health:
					self.stream_health[port] = str(status.get("state", "failed"))
					reason = status.get("reason", "unknown")
					logger.info(f"[stream-{port}] status update: {self.stream_health[port]} ({reason})")

			alive_count = sum(1 for p in self.processes if p.is_alive())
			if alive_count == 0:
				if self.never_give_up:
					logger.warning("All camera processes stopped; restarting every stream...")
					self.processes = [
						_spawn_streamer_process(sub_streamer, self.stop_event, self.status_queue)
						for sub_streamer in self.streamers
					]
					for sub_streamer in self.streamers:
						self.stream_health[sub_streamer.config.port] = "starting"
					for sub_streamer in self.streamers:
						logger.info(
							f"Attempting to restart camera {sub_streamer.config.camera_id} on port {sub_streamer.config.port}"
						)
					for _ in self.streamers:
						time.sleep(STARTUP_STAGGER_SECONDS)
					continue

				logger.error("All camera processes stopped. Exiting streamer.")
				self.stop_event.set()
				break

			for idx, p in enumerate(self.processes):
				if p.is_alive():
					continue

				p.join(timeout=0)
				sub_streamer = self.streamers[idx]
				logger.warning(
					f"[stream-{sub_streamer.config.port}] process exited unexpectedly (code={p.exitcode}); restarting"
				)
				self.stream_health[sub_streamer.config.port] = "starting"
				self.processes[idx] = _spawn_streamer_process(sub_streamer, self.stop_event, self.status_queue)
				time.sleep(STARTUP_STAGGER_SECONDS)

			if not self.never_give_up and self.stream_health and all(state == "failed" for state in self.stream_health.values()):
				logger.error("All cameras are failing. Exiting streamer.")
				self.stop_event.set()
				break

			time.sleep(0.1)
