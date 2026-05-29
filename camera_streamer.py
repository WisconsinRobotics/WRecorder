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
	MosaicStreamer,
	MosaicConfig,
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


def run_udp_control_server(control_port: int, control_queues: dict):
	"""Listens for UDP control messages from receivers (e.g., subscription requests) and routes to queues."""
	server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		server_socket.bind(("0.0.0.0", control_port))
		server_socket.settimeout(1.0)
		logger.info(f"UDP Control Server listening on port {control_port}")
		while True:
			try:
				data, addr = server_socket.recvfrom(2048)
				try:
					payload = json.loads(data.decode("utf-8"))
					if payload.get("type") == "SUBSCRIBE_REQUEST":
						receiver_ip = payload.get("receiver_ip")
						ports = payload.get("ports", [])
						for port in ports:
							if port in control_queues:
								control_queues[port].put({"type": "add_client", "ip": receiver_ip, "port": port})
				except json.JSONDecodeError:
					pass
			except socket.timeout:
				continue
	except Exception as e:
		logger.error(f"UDP Control Server error: {e}")
	finally:
		server_socket.close()


def announce_stream_config(
	stop_event: multiprocessing.Event,
	streamer_name: str,
	streamer_ip: str,
	multicast_ip: str,
	base_port: int,
	camera_ids: List[int],
	discovery_port: int,
	discovery_interval: float,
	stream_count: int = None,
	mosaic: bool = False,
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
		"stream_count": len(camera_ids) if stream_count is None else stream_count,
		"camera_ids": camera_ids,
		"mosaic": mosaic,
	}

	print(json.dumps(payload, indent=2))

	stream_count_value = len(camera_ids) if stream_count is None else stream_count

	logger.info(
		f"[discovery] Announcing '{streamer_name}' on UDP {discovery_port} "
		f"(base_port={base_port}, streams={stream_count_value}, mosaic={mosaic})"
	)
	logger.info(
		f"[discovery] payload: streamer_ip={streamer_ip}, camera_ids={camera_ids}, "
		f"interval={interval:.2f}s, version={DISCOVERY_VERSION}, mosaic={mosaic}"
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
	simulate_loss = args.simulate_loss
	mosaic_enabled = args.mosaic.lower() == "on"
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

	output_stream_count = 1 if mosaic_enabled else len(camera_ids)
	max_port = base_port + output_stream_count - 1
	if not has_valid_sequential_port_range(base_port, output_stream_count):
		logger.error(
			f"Invalid port range: base-port={base_port} with {output_stream_count} streams "
			f"would exceed {VALID_PORT_MAX} (max={max_port})."
		)
		exit(2)
		
	control_queues = {port: multiprocessing.Queue() for port in range(base_port, max_port + 1)}
	control_server = threading.Thread(
		target=run_udp_control_server,
		args=(args.control_port, control_queues),
		daemon=True
	)
	control_server.start()

	streamer_stop_event = None
	stream_processes = []
	if mosaic_enabled:
		mosaic_camera_count = len(camera_ids)
		mosaic_bitrate = bitrate * mosaic_camera_count
		streamer_stop_event = multiprocessing.Event()
		status_queue = multiprocessing.Queue()
		mosaic_streamer = MosaicStreamer(
			MosaicConfig(
				output_port=base_port,
				camera_ids=camera_ids,
				bitrate=mosaic_bitrate,
				target_fps=target_fps,
				multicast_ip=MULTICAST_IP,
				simulation=simulate_cameras is not None,
				simulate_loss=simulate_loss,
				control_queue=control_queues[base_port],
			),
		)
		stream_process = multiprocessing.Process(
			target=mosaic_streamer.start,
			args=(streamer_stop_event, status_queue),
			daemon=True,
		)
		stream_process.start()
		stream_processes.append(stream_process)
		logger.info(
			f"Attempting to start mosaic stream for cameras {camera_ids} on port {base_port} "
			f"with bitrate={mosaic_bitrate}"
		)
	else:
		streamer = MultiStreamer(
			base_port,
			camera_ids,
			bitrate,
			target_fps,
			MULTICAST_IP,
			simulation=simulate_cameras is not None,
			simulate_loss=simulate_loss,
			never_give_up=never_give_up,
			control_queues=control_queues,
		)
		streamer.start()
		streamer_stop_event = streamer.stop_event
		stream_processes = streamer.processes

	discovery_thread = None
	if announce_discovery:
		streamer_ip = resolve_local_ip(args.only_eth0)
		discovery_thread = threading.Thread(
			target=announce_stream_config,
			args=(
				streamer_stop_event,
				streamer_name,
				streamer_ip,
				MULTICAST_IP,
				base_port,
				camera_ids,
				discovery_port,
				discovery_interval,
				len(camera_ids),
				mosaic_enabled,
			),
			daemon=True,
		)
		discovery_thread.start()

	ros2_thread = threading.Thread(
		target=ros2_command_thread,
		args=(camera_ids, streamer_stop_event),
		daemon=True,
	)
	ros2_thread.start()

	install_stop_signal_handlers(streamer_stop_event.set, logger, "Stopping streams...")

	try:
		if mosaic_enabled:
			while not streamer_stop_event.is_set():
				for p in stream_processes:
					if not p.is_alive():
						logger.error(f"Mosaic streamer process {p.pid} exited unexpectedly (code={p.exitcode})")
						streamer_stop_event.set()
						break
				time.sleep(0.1)
		else:
			streamer.supervise()
	except KeyboardInterrupt:
		logger.info("Keyboard interrupt received. Stopping streams...")
		streamer_stop_event.set()
	finally:
		streamer_stop_event.set()
		for p in stream_processes:
			p.join(timeout=1.0)
			if p.is_alive():
				logger.warning(f"Streamer process {p.pid} did not exit cleanly, terminating...")
				p.terminate()
				p.join()

	if discovery_thread is not None:
		discovery_thread.join(timeout=1)

	ros2_thread.join(timeout=1)

	logger.info("All streams stopped")
