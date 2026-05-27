import time
import multiprocessing
import json
import socket
import threading
from typing import List
import os
try:
	import rclpy
	from rclpy.node import Node
	from std_msgs.msg import String
	HAVE_ROS2 = True
except ImportError:
	HAVE_ROS2 = False
	Node = object # Just so class definition doesn't throw syntax error

from common_utils import (
	get_logger,
	DISCOVERY_MESSAGE_TYPE,
	DISCOVERY_VERSION,
	VALID_PORT_MAX,
	are_non_negative_ints,
	has_valid_sequential_port_range,
	install_stop_signal_handlers,
	MULTICAST_IP,
)

from streamer_utils import (
	resolve_local_ip,
	handle_arguments,
	MultiStreamer,
)

logger = get_logger(__name__)

DISCOVERY_MIN_INTERVAL_SECONDS = 0.1
WARN_EVERY_N_FAILURES = 25
CAMERA_SCAN_START = 0
CAMERA_SCAN_STOP = 8
CAMERA_SCAN_STEP = 2

class CameraCommandNode(Node):
	def __init__(self, camera_ids):
		super().__init__('camera_transmitter')
		self.camera_ids = camera_ids

		self.subscription = self.create_subscription(String, '/camera_commands', self.cmd_callback, 10)
	
	def cmd_callback(self, msg):
		command = msg.data
		if command.startswith("EXP:"):
			exp_val_str = command.split(":")[1]
			if exp_val_str == "AUTO":
				self.get_logger().info("Setting exposure to AUTO")
				for cam_id in self.camera_ids:
					os.system(f"v4l2-ctl -d /dev/video{cam_id} -c auto_exposure=3")
			else:
				try:
					exposure_val = int(exp_val_str)
					self.get_logger().info(f"Setting exposure to {exposure_val}")

					for cam_id in self.camera_ids:
						os.system(f"v4412-ctl -d /dev/video{cam_id} -c auto_exposure=1")
						os.system(f"v4l2-ctl -d /dev/video{cam_id} -c exposure_time_absolute={exposure_val}")
				except ValueError:
					self.get_logger().info("Obtained value error make sure correct exposure value was passed in")
	
def ros2_command_thread(camera_ids, stop_event):
	if not HAVE_ROS2:
		logger.warning("ROS2 (rclpy) could not be imported. Commands will be disabled.")
		return
	try:
		rclpy.init(args=None)
		node = CameraCommandNode(camera_ids)
		while not stop_event.is_set():
			rclpy.spin_once(node, timeout_sec=0.5)
	except Exception as e:
		logger.error(f"ROS2 node exception: {e}")
	finally:
		if HAVE_ROS2 and rclpy.ok():
			try:
				node.destroy_node()
				rclpy.shutdown()
			except Exception:
				pass


def announce_stream_config(
	stop_event: multiprocessing.Event,
	streamer_name: str,
	streamer_ip: str,
	multicast_ip: str,
	base_port: int,
	camera_ids: List[int],
	discovery_port: int,
	discovery_interval: float,
):
	"""Broadcast stream configuration over UDP for receiver auto-configuration."""
	if not are_non_negative_ints(camera_ids, require_non_empty=True):
		raise ValueError("camera_ids must be a non-empty list of non-negative integers")

	announce_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	announce_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

	interval = max(DISCOVERY_MIN_INTERVAL_SECONDS, discovery_interval)
	payload = {
		"type": DISCOVERY_MESSAGE_TYPE,
		"version": DISCOVERY_VERSION,
		"streamer_name": streamer_name,
		"streamer_ip": streamer_ip,
		"base_port": base_port,
		"stream_count": len(camera_ids),
		"camera_ids": camera_ids,
	}

	logger.info(
		f"[discovery] Announcing '{streamer_name}' on UDP {discovery_port} "
		f"(base_port={base_port}, streams={len(camera_ids)})"
	)
	logger.info(
		f"[discovery] payload: streamer_ip={streamer_ip}, camera_ids={camera_ids}, "
		f"interval={interval:.2f}s, version={DISCOVERY_VERSION}"
	)

	try:
		while not stop_event.is_set():
			payload["announced_at"] = time.time()
			packet = json.dumps(payload).encode("utf8")
			try:
				announce_socket.sendto(packet, ("255.255.255.255", discovery_port))
			except OSError as exc:
				logger.error(
					f"[discovery] announce failed: {exc} "
					f"(target=255.255.255.255:{discovery_port}, streamer_ip={streamer_ip})"
				)
			stop_event.wait(interval)
	finally:
		announce_socket.close()


def find_available_cameras() -> List[int]:
	available_cameras = []
	for cam_id in range(CAMERA_SCAN_START, CAMERA_SCAN_STOP, CAMERA_SCAN_STEP):
		device_path = f"/dev/video{cam_id}"
		if os.path.exists(device_path):
			available_cameras.append(cam_id)
	return available_cameras


if __name__ == "__main__":
	try:
		multiprocessing.set_start_method("spawn")
	except RuntimeError:
		pass
	args = handle_arguments()

	base_port = args.base_port
	camera_ids = args.camera_ids
	bitrate = args.bitrate
	target_fps = args.target_fps
	simulate_cameras = args.simulate_cameras
	streamer_name = args.streamer_name
	announce_discovery = args.announce_discovery.lower() == "on"
	discovery_port = args.discovery_port
	discovery_interval = args.discovery_interval
	never_give_up = args.never_give_up.lower() == "on"

	if simulate_cameras is not None:
		camera_ids = list(range(simulate_cameras))
	elif args.auto_find_cameras.lower() == "on":
		camera_ids = find_available_cameras()

	if not are_non_negative_ints(camera_ids):
		logger.error("camera-ids must only contain non-negative integers")
		exit(2)

	if not camera_ids:
		logger.error("No available cameras found. Exiting.")
		exit(1)

	max_port = base_port + len(camera_ids) - 1
	if not has_valid_sequential_port_range(base_port, len(camera_ids)):
		logger.error(
			f"Invalid port range: base-port={base_port} with {len(camera_ids)} streams "
			f"would exceed {VALID_PORT_MAX} (max={max_port})."
		)
		exit(2)

	streamer = MultiStreamer(
		base_port,
		camera_ids,
		bitrate,
		target_fps,
		MULTICAST_IP,
		simulation=simulate_cameras is not None,
		never_give_up=never_give_up,
	)
	streamer.start()

	discovery_thread = None
	if announce_discovery:
		streamer_ip = resolve_local_ip()
		discovery_thread = threading.Thread(
			target=announce_stream_config,
			args=(
				streamer.stop_event,
				streamer_name,
				streamer_ip,
				MULTICAST_IP,
				base_port,
				camera_ids,
				discovery_port,
				discovery_interval,
			),
			daemon=True,
		)
		discovery_thread.start()

	ros2_thread = threading.Thread(
		target=ros2_command_thread,
		args=(camera_ids, streamer.stop_event),
		daemon=True
	)
	ros2_thread.start()

	install_stop_signal_handlers(streamer.stop_event.set, logger, "Stopping streams...")

	try:
		streamer.supervise()
	except KeyboardInterrupt:
		logger.info("Keyboard interrupt received. Stopping streams...")
		streamer.stop_event.set()
	finally:
		streamer.stop_event.set()
		for p in streamer.processes:
			p.join(timeout=1.0)
			if p.is_alive():
				logger.warning(f"Streamer process {p.pid} did not exit cleanly, terminating...")
				p.terminate()
				p.join()

	if discovery_thread is not None:
		discovery_thread.join(timeout=1)

	ros2_thread.join(timeout=1)

	logger.info("All streams stopped")
