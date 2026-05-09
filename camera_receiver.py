from common_utils import (
	get_logger,
	build_sequential_ports,
	install_stop_signal_handlers,
	parse_discovery_payload,
	clamp,
	MULTICAST_IP,
)

import socket
import time
from receiver_utils import handle_arguments, MultiReceiver, StreamDisplayWidget
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QCoreApplication

logger = get_logger(__name__)

DISCOVERY_SOCKET_TIMEOUT_SECONDS = 0.25
DISCOVERY_BUFFER_SIZE_BYTES = 4096
MIN_TIMEOUT_SECONDS = 1.0
DISCOVERY_TIMEOUT_SECONDS = 5.0
WARN_EVERY_N_FAILURES = 50
DISPLAY_UPDATE_MS = 30
GRID_COLS = 4

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
		f"Receiver config: multicast_ip={MULTICAST_IP}, base_port={base_port}, "
		f"count={stream_count}, timeout={connection_timeout:.1f}s"
	)

	receiver = MultiReceiver(ports, connection_timeout, window_prefix)
	receiver.start()

	install_stop_signal_handlers(receiver.stop_event.set, logger, "Stopping receivers...")

	# Create PyQt application
	app = QApplication([])
	window = StreamDisplayWidget(receiver, GRID_COLS)
	window.show()

	logger.info("PyQt6 display window opened")

	# Start update timer
	timer = QTimer()
	timer.timeout.connect(window.update_frames)
	timer.start(DISPLAY_UPDATE_MS)

	# Poll for external stop_event (SIGINT/SIGTERM) and quit Qt when set
	poll_timer = QTimer()
	poll_timer.timeout.connect(lambda: QCoreApplication.quit() if receiver.stop_event.is_set() else None)
	poll_timer.start(100)

	# Run event loop
	try:
		exit_code = app.exec()
	except KeyboardInterrupt:
		logger.info("Keyboard interrupt received.")
		exit_code = 0
	finally:
		logger.info("Stopping receivers...")
		receiver.stop()
		timer.stop()
		try:
			poll_timer.stop()
		except Exception:
			pass

	logger.info("All receivers stopped")
