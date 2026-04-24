import cv2
import socket
from typing import Optional
import time
from common_utils import (
	get_logger,
	build_sequential_ports,
	install_stop_signal_handlers,
	parse_discovery_payload,
	clamp,
)
from receiver_utils import handle_arguments, MultiReceiver
import os

os.environ["QT_QPA_FONTDIR"] = "./fonts"

logger = get_logger(__name__)


DISCOVERY_SOCKET_TIMEOUT_SECONDS = 0.25
DISCOVERY_BUFFER_SIZE_BYTES = 4096
MIN_TIMEOUT_SECONDS = 1.0
DISCOVERY_TIMEOUT_SECONDS = 5.0
WARN_EVERY_N_FAILURES = 50
DISPLAY_WAIT_KEY_MS = 30


def discover_stream_config(
	discovery_port: int, timeout: float, streamer_name_filter: str = None
):
	"""Listen for UDP discovery heartbeats and return the first matching stream config."""
	receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	receiver_socket.bind(("", discovery_port))
	receiver_socket.settimeout(DISCOVERY_SOCKET_TIMEOUT_SECONDS)

	logger.info(
		f"Listening for discovery on UDP {discovery_port} for up to {timeout:.1f}s"
		f"{f' (filter={streamer_name_filter})' if streamer_name_filter else ''}..."
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

			logger.info(
				f"Discovered '{discovered['streamer_name']}' at {discovered['streamer_ip']} "
				f"(base_port={discovered['base_port']}, streams={discovered['stream_count']})"
			)
			return discovered
	finally:
		receiver_socket.close()

	return None


if __name__ == "__main__":
	args = handle_arguments()

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
		discovered = discover_stream_config(
			discovery_port, DISCOVERY_TIMEOUT_SECONDS, streamer_name_filter
		)
		connection_timeout = clamp(
			timeout - (time.time() - setup_start), MIN_TIMEOUT_SECONDS, timeout
		)

		if discovered is not None:
			broadcast_ip = discovered["streamer_ip"]
			base_port = discovered["base_port"]
			stream_count = discovered["stream_count"]
			window_prefix = discovered["streamer_name"]
		else:
			logger.warning(
				"No matching discovery packet found. Falling back to manual args."
			)

	ports = build_sequential_ports(base_port, stream_count)
	if ports is None:
		logger.error(
			f"Invalid port configuration: base_port={base_port}, count={stream_count}"
		)
		exit(2)

	logger.info(
		f"Receiver config: ip={broadcast_ip}, base_port={base_port}, "
		f"count={stream_count}, timeout={connection_timeout:.1f}s"
	)

	receiver = MultiReceiver(broadcast_ip, ports, connection_timeout, window_prefix)
	receiver.start()

	install_stop_signal_handlers(receiver.stop_event.set, logger, "Stopping receivers...")

	logger.info("Press 'q' in any window to quit")

	try:
		# Main loop handles displaying frames so GUI calls happen on main thread
		while (
			any(t.is_alive() for t in receiver.threads)
			and not receiver.stop_event.is_set()
		):
			stream_names = receiver.get_stream_names()
			for name in stream_names:
				frame = receiver.get_frame(name)
				if frame is None:
					continue
				cv2.imshow(name, frame)
			if cv2.waitKey(DISPLAY_WAIT_KEY_MS) & 0xFF == ord("q"):
				logger.info("Quit key pressed. Stopping receivers...")
				receiver.stop()
				break
	except KeyboardInterrupt:
		logger.info("Keyboard interrupt received. Stopping receivers...")
		receiver.stop()

	# destroy any remaining windows
	try:
		cv2.destroyAllWindows()
	except Exception:
		pass

	logger.info("All receivers stopped")
