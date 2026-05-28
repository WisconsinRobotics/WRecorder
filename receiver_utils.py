import argparse
from common_utils import (
	apply_required_external_defaults,
	get_logger,
	CAMERA_FRAME_WIDTH,
	CAMERA_FRAME_HEIGHT,
)
import threading
from typing import List, Optional
import numpy as np
import time
from PyQt6.QtWidgets import QMainWindow, QLabel, QApplication, QWidget, QVBoxLayout
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt, pyqtSlot, QCoreApplication, QObject, QEvent
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


class QuitFilter(QObject):
	def __init__(self, receiver):
		super().__init__()
		self.receiver = receiver

	def eventFilter(self, obj, event):
		if event.type() == QEvent.Type.KeyPress:
			try:
				if event.key() == Qt.Key.Key_Q:
					self.receiver.stop()
					QCoreApplication.quit()
					return True
			except Exception:
				pass
		return False

logger = get_logger(__name__)

# Initialize GStreamer
Gst.init(None)

GSTREAMER_CONNECTION_POLL_INTERVAL_SECONDS = 0.1

def handle_arguments():
	parser = argparse.ArgumentParser(
		prog="camera_receiver",
		description="Receive one or more camera streams using GStreamer Multicast",
	)
	parser.add_argument("--broadcast-ip", type=str, help="Legacy IP argument (ignored for multicast)")
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
		"--control-port", type=int, help="UDP port used for subscribing to unicast streams"
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
	def __init__(self, port: int, timeout: float, stop_event: threading.Event, frame_store: FrameStore, window_prefix: str):
		self.port = port
		self.timeout = timeout
		self.stop_event = stop_event
		self.frame_store = frame_store
		self.window_prefix = window_prefix
		self.pipeline = None
		self.appsink = None
		self._first_frame_event = threading.Event()
		self._appsink_handler_id = None
		self._stop_requested = False

	def start(self):
		pipeline_str = (
			f"udpsrc port={self.port} buffer-size=2097152 ! "
			"application/x-rtp,media=video,clock-rate=90000,payload=96,encoding-name=H264 ! "
			"rtpjitterbuffer latency=100 ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! appsink name=appsink emit-signals=true max-buffers=5 drop=true sync=false"
		)
		window_name = f"{self.window_prefix}-{self.port}"
		logger.info(f"[{window_name}] Attempting to listen on UDP port {self.port} (unicast)...")
		logger.info(f"[{window_name}] Pipeline: {pipeline_str}")

		try:
			self.pipeline = Gst.parse_launch(pipeline_str)
			self.appsink = self.pipeline.get_by_name("appsink")

			if self.appsink is None:
				logger.error(f"[{window_name}] Failed to find appsink element in pipeline")
				return

			# connect appsink new-sample callback
			try:
				self._appsink_handler_id = self.appsink.connect("new-sample", self._on_new_sample)
			except Exception:
				# fallback - some bindings may require using 'connect' via GObject
				try:
					self._appsink_handler_id = self.appsink.connect("new-sample", self._on_new_sample)
				except Exception as e:
					logger.error(f"[{window_name}] Failed to connect appsink handler: {e}")
					self._appsink_handler_id = None

			# Set to PLAYING state
			ret = self.pipeline.set_state(Gst.State.PLAYING)
			if ret == Gst.StateChangeReturn.FAILURE:
				logger.error(f"[{window_name}] Failed to set pipeline to PLAYING state")
				return
		except Exception as e:
			logger.error(f"[{window_name}] Failed to create GStreamer pipeline: {e}")
			return

		connection_timeout = self.timeout
		

		# Wait for first frame (set by callback)
		if not self._first_frame_event.wait(timeout=connection_timeout):
			logger.error(f"Failed to receive stream on port {self.port} (timeout after {connection_timeout}s)")
			# ensure cleanup
			self.stop()
			return

		logger.info(f"Receiving stream on port {self.port} -> window '{window_name}'")

		# Keep thread alive until stop is requested
		try:
			while not self.stop_event.is_set():
				time.sleep(GSTREAMER_CONNECTION_POLL_INTERVAL_SECONDS)
		finally:
			self.frame_store.remove_stream(window_name)
			if self.pipeline is not None:
				try:
					self.pipeline.set_state(Gst.State.NULL)
				except Exception:
					pass

	def _process_sample(self, sample, window_name: str, current_frame_store_failures: int) -> int:
		"""Process a GStreamer sample and store the frame. Returns updated failure count."""
		try:
			buffer = sample.get_buffer()
			caps = sample.get_caps()
            
			# Extract frame data
			success, mapinfo = buffer.map(Gst.MapFlags.READ)
			if not success:
				return current_frame_store_failures
            
			try:
				# Get frame dimensions from caps
				structure = caps.get_structure(0)
				width = structure.get_value("width")
				height = structure.get_value("height")
                
				# Create numpy array from buffer data
				frame_data = np.frombuffer(mapinfo.data, dtype=np.uint8)
				frame = frame_data.reshape((height, width, 3))
                
				# Store frame
				frame_store_error = self.frame_store.set_latest(window_name, frame)
				if frame_store_error is not None:
					current_frame_store_failures += 1
					logger.error(f"[{window_name}] frame store failed (failure #{current_frame_store_failures}): {frame_store_error}")
				return current_frame_store_failures
			finally:
				buffer.unmap(mapinfo)
		except Exception as e:
			logger.error(f"[{window_name}] Error processing sample: {e}")
			return current_frame_store_failures

	def _on_new_sample(self, appsink):
		"""GStreamer appsink 'new-sample' callback."""
		try:
			sample = appsink.emit("pull-sample")
			if sample is None:
				return Gst.FlowReturn.OK
			window_name = f"{self.window_prefix}-{self.port}"
			# Reuse _process_sample logic but adapt to callback semantics
			# Note: _process_sample expects (sample, window_name, failure_count)
			self._process_sample(sample, window_name, 0)
			# mark first frame received
			if not self._first_frame_event.is_set():
				self._first_frame_event.set()
			return Gst.FlowReturn.OK
		except Exception as e:
			logger.error(f"[{self.window_prefix}-{self.port}] Error in new-sample callback: {e}")
			return Gst.FlowReturn.OK

	def stop(self):
		"""Stop the receiver: disconnect callbacks and set pipeline to NULL."""
		self._stop_requested = True
		try:
			if self.appsink is not None and self._appsink_handler_id is not None:
				try:
					self.appsink.disconnect(self._appsink_handler_id)
				except Exception:
					pass
			self._appsink_handler_id = None
			if self.pipeline is not None:
				try:
					self.pipeline.set_state(Gst.State.NULL)
				except Exception:
					pass
		except Exception:
			pass
 


class StreamDisplayWidget(QMainWindow):
	"""Controller that creates one top-level window per stream (simple multi-window mode)."""
	def __init__(self, receiver, grid_cols: int = 4):
		super().__init__()
		self.receiver = receiver
		self.grid_cols = grid_cols
		# Map stream_name -> (window, label, event_filter)
		self.stream_windows: dict[str, tuple[QMainWindow, QLabel, QObject]] = {}
		self.setWindowTitle("WRecorder - Stream Display")
		# Keep a small controller window (can be minimized)
		self.resize(320, 40)

	def _compute_window_size(self) -> tuple[int, int]:
		"""Compute a sensible default size for per-stream windows based on screen and camera size."""
		screen = QApplication.primaryScreen()
		if screen is None:
			return CAMERA_FRAME_WIDTH, CAMERA_FRAME_HEIGHT
		avail = screen.availableGeometry()
		max_w = max(320, min(CAMERA_FRAME_WIDTH, avail.width() // 2))
		max_h = max(240, min(CAMERA_FRAME_HEIGHT, avail.height() // 2))
		return int(max_w), int(max_h)

	@pyqtSlot()
	def update_frames(self):
		current_names = set(self.stream_windows.keys())
		new_names_list = self.receiver.get_sorted_stream_names()
		new_names = set(new_names_list)

		# Create windows for newly discovered streams
		for name in new_names - current_names:
			win = QMainWindow()
			win.setWindowTitle(name)
			label = QLabel()
			label.setAlignment(Qt.AlignmentFlag.AlignCenter)
			w, h = self._compute_window_size()
			label.setFixedSize(w, h)
			# Put label in a container with zero margins so the label area matches desired size
			container = QWidget()
			layout = QVBoxLayout()
			layout.setContentsMargins(0, 0, 0, 0)
			layout.setSpacing(0)
			layout.addWidget(label)
			container.setLayout(layout)
			win.setCentralWidget(container)
			# Resize window to match content size (label). Avoid arbitrary extra offsets.
			win.resize(w, h)
			win.show()
			# Install an event filter so pressing 'Q' in this window quits the app
			try:
				filter_obj = QuitFilter(self.receiver)
				win.installEventFilter(filter_obj)
			except Exception:
				filter_obj = None
			self.stream_windows[name] = (win, label, filter_obj)

		# Remove windows for disconnected streams
		for name in list(current_names - new_names):
			win, label, filt = self.stream_windows.pop(name)
			try:
				if filt is not None:
					try:
						win.removeEventFilter(filt)
					except Exception:
						pass
				win.close()
			except Exception:
				pass

		# Update frames for existing windows in sorted order
		for name in new_names_list:
			pair = self.stream_windows.get(name)
			if not pair:
				continue
			win, label = pair[0], pair[1]
			frame = self.receiver.get_frame(name)
			if frame is None:
				# show waiting text
				label.setText(f"Waiting for {name}...")
				label.setPixmap(QPixmap())
				continue
			# Convert BGR -> RGB and to QImage
			height, width = frame.shape[:2]
			rgb_frame = frame[:, :, ::-1]
			frame_bytes = rgb_frame.tobytes()
			q_img = QImage(frame_bytes, width, height, 3 * width, QImage.Format.Format_RGB888)
			pix = QPixmap.fromImage(q_img)
			# Scale to label size keeping aspect ratio
			lw = label.width()
			lh = label.height()
			pix = pix.scaled(lw, lh, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
			label.setPixmap(pix)
	def keyPressEvent(self, event):
		"""Handle key press events."""
		if event.key() == Qt.Key.Key_Q:
			logger.info("Quit key pressed. Stopping receivers...")
			self.receiver.stop()
			# Ensure Qt main loop exits
			try:
				QCoreApplication.quit()
			except Exception:
				self.close()
		else:
			super().keyPressEvent(event)
	
	def closeEvent(self, event):
		"""Handle window close event."""
		logger.info("Window closed. Stopping receivers...")
		self.receiver.stop()
		event.accept()


class MultiReceiver:
	def __init__(self, ports: List[int], timeout: float, window_prefix: str):
		self.ports = ports
		self.timeout = timeout
		self.window_prefix = window_prefix

		self.stop_event = threading.Event()
		self.threads: List[threading.Thread] = []
		self.frame_store = FrameStore()

		self.sub_receivers: List[SingleReceiver] = []

	def start(self):
		for port in self.ports:
			sub_receiver = SingleReceiver(port, self.timeout, self.stop_event, self.frame_store, self.window_prefix)
			t = threading.Thread(target=sub_receiver.start)
			t.start()
			self.threads.append(t)
			self.sub_receivers.append(sub_receiver)

	def stop(self):
		# Signal receivers to stop, call each sub.stop(), and ensure GStreamer pipelines are set to NULL
		self.stop_event.set()
		for sub in self.sub_receivers:
			try:
				sub.stop()
			except Exception:
				pass
		# Join threads with timeout
		for t in self.threads:
			t.join(timeout=5.0)

	def get_frame(self, stream_name: str):
		return self.frame_store.get_frame(stream_name)

	def get_stream_names(self) -> List[str]:
		return self.frame_store.snapshot_keys()
	
	def _extract_port_from_stream_name(self, stream_name: str) -> int:
		"""Extract port number from stream name (format: prefix-port)."""
		try:
			port_str = stream_name.split('-')[-1]
			return int(port_str)
		except (ValueError, IndexError):
			return 0
	
	def get_sorted_stream_names(self) -> List[str]:
		"""Get stream names sorted by port number."""
		names = self.get_stream_names()
		return sorted(names, key=self._extract_port_from_stream_name)