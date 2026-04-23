import cv2
import zmq
import sys
import time
import argparse
import multiprocessing
import json
import socket
import threading
from typing import List
import os
import numpy as np
from common_utils import (
	ANSI_GREEN,
	ANSI_RED,
	ANSI_YELLOW,
	DISCOVERY_MESSAGE_TYPE,
	DISCOVERY_VERSION,
	VALID_PORT_MAX,
	VALID_PORT_MIN,
	are_non_negative_ints,
	apply_required_external_defaults,
	has_valid_sequential_port_range,
	color_text,
	install_stop_signal_handlers,
	rate_limited_warn,
	int_in_range,
)
from streamer_utils import resolve_local_ip, BroadcastConfig, Camera, ZMQHandler, handle_arguments


DISCOVERY_MIN_INTERVAL_SECONDS = 0.1
CAMERA_BACKOFF_BASE_SECONDS = 0.2
CAMERA_MAX_BACKOFF_SECONDS = 10
WARN_EVERY_N_FAILURES = 25
STARTUP_STAGGER_SECONDS = 1
CAMERA_SCAN_START = 0
CAMERA_SCAN_STOP = 8
CAMERA_SCAN_STEP = 2


def announce_stream_config(
	stop_event: multiprocessing.Event,
	streamer_name: str,
	streamer_ip: str,
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

	print(
		color_text(
			f"[discovery] Announcing '{streamer_name}' on UDP {discovery_port} "
			f"(base_port={base_port}, streams={len(camera_ids)})",
			ANSI_GREEN,
		)
	)
	print(
		color_text(
			f"[discovery] payload: streamer_ip={streamer_ip}, camera_ids={camera_ids}, "
			f"interval={interval:.2f}s, version={DISCOVERY_VERSION}",
			ANSI_GREEN,
		)
	)

	try:
		while not stop_event.is_set():
			payload["announced_at"] = time.time()
			packet = json.dumps(payload).encode("utf8")
			try:
				announce_socket.sendto(packet, ("255.255.255.255", discovery_port))
			except OSError as exc:
				print(
					color_text(
						f"[discovery] announce failed: {exc} "
						f"(target=255.255.255.255:{discovery_port}, streamer_ip={streamer_ip})",
						ANSI_RED,
					)
				)
			stop_event.wait(interval)
	finally:
		announce_socket.close()


def broadcast_camera_data(config: BroadcastConfig, stop_event: multiprocessing.Event):
	# Publish frames from a single camera on tcp://*:{port} until stop_event is set.
	zmq_handler = ZMQHandler(config)
	camera = Camera(config)

	frame_count = 0
	frame_interval = 1.0 / config.target_fps

	try:
		failed_frame_count = 0
		while not stop_event.is_set():
			frame_start = time.time()

			success, encoded_frame = camera.get_encoded_frame()
			if not success or encoded_frame is None:
				rate_limited_warn(
					failed_frame_count,
					f"[stream-{config.port}] failed to get encoded frame",
					sleep=True,
					max_sleep_seconds=CAMERA_MAX_BACKOFF_SECONDS,
					backoff_base_seconds=CAMERA_BACKOFF_BASE_SECONDS,
				)
				continue
			failed_frame_count = 0  # reset on success

			success = zmq_handler.send_frame(encoded_frame)
			if not success:
				continue
			frame_count += 1

			frame_end = time.time()
			elapsed = frame_end - frame_start
			sleep_time = frame_interval - elapsed
			if sleep_time > 0:
				time.sleep(sleep_time)

	finally:
		print(
			f"[stream-{config.port}] cleaning up camera and socket (frames sent: {frame_count})"
		)

		camera.release()
		zmq_handler.close()


def start_multiple_streams(
	base_port: int,
	camera_ids: List[int],
	jpg_quality: int,
	target_fps: int,
	simulation: bool = False,
):
	###
	# Start a publisher for each camera_id on ports base_port + index.
	#
	# Returns (stop_event, threads).
	###
	stop_event = multiprocessing.Event()
	processes: List[multiprocessing.Process] = []

	for idx, cam_id in enumerate(camera_ids):
		port = base_port + idx
		print(f"[main] Starting thread for camera {cam_id} on port {port}")
		broadcast_config = BroadcastConfig(
			port=port,
			camera_id=cam_id,
			jpg_quality=jpg_quality,
			target_fps=target_fps,
			simulation=simulation,
		)
		p = multiprocessing.Process(
			target=broadcast_camera_data,
			args=(broadcast_config, stop_event),
			daemon=True,
		)
		p.start()
		processes.append(p)
		print(
			color_text(
				f"Attempting to start camera {cam_id} on port {port}", ANSI_YELLOW
			)
		)

		time.sleep(STARTUP_STAGGER_SECONDS)  # slight delay to stagger startups

	return stop_event, processes


def find_available_cameras() -> List[int]:
	available_cameras = []
	for cam_id in range(CAMERA_SCAN_START, CAMERA_SCAN_STOP, CAMERA_SCAN_STEP):
		if f"video{cam_id}" not in os.listdir("/dev"):
			continue
		cap = cv2.VideoCapture(cam_id)
		if cap.isOpened():
			available_cameras.append(cam_id)
			cap.release()
	return available_cameras


if __name__ == "__main__":
	args = handle_arguments()

	base_port = args.base_port
	camera_ids = args.camera_ids
	jpg_quality = args.jpg_quality
	target_fps = args.target_fps
	simulate_cameras = args.simulate_cameras
	streamer_name = args.streamer_name
	announce_discovery = args.announce_discovery.lower() == "on"
	discovery_port = args.discovery_port
	discovery_interval = args.discovery_interval

	if simulate_cameras is not None:
		camera_ids = list(range(simulate_cameras))
	elif args.auto_find_cameras.lower() == "on":
		camera_ids = find_available_cameras()

	if not are_non_negative_ints(camera_ids):
		print(
			color_text("camera-ids must only contain non-negative integers", ANSI_RED)
		)
		exit(2)

	if not camera_ids:
		print(color_text("No available cameras found. Exiting.", ANSI_RED))
		exit(1)

	max_port = base_port + len(camera_ids) - 1
	if not has_valid_sequential_port_range(base_port, len(camera_ids)):
		print(
			color_text(
				f"Invalid port range: base-port={base_port} with {len(camera_ids)} streams "
				f"would exceed {VALID_PORT_MAX} (max={max_port}).",
				ANSI_RED,
			)
		)
		exit(2)

	stop_event, processes = start_multiple_streams(
		base_port,
		camera_ids,
		jpg_quality,
		target_fps,
		simulation=simulate_cameras is not None,
	)

	discovery_thread = None
	if announce_discovery:
		print("[discovery] ")
		streamer_ip = resolve_local_ip()
		discovery_thread = threading.Thread(
			target=announce_stream_config,
			args=(
				stop_event,
				streamer_name,
				streamer_ip,
				base_port,
				camera_ids,
				discovery_port,
				discovery_interval,
			),
			daemon=True,
		)
		discovery_thread.start()

	install_stop_signal_handlers(stop_event.set, "Stopping streams...")

	try:
		for p in processes:
			p.join()
	except KeyboardInterrupt:
		print(
			color_text("Keyboard interrupt received. Stopping streams...", ANSI_YELLOW)
		)
		stop_event.set()
		for p in processes:
			p.join(timeout=2)
			if p.is_alive():
				p.terminate()

	if discovery_thread is not None:
		discovery_thread.join(timeout=1)

	print(color_text("All streams stopped", ANSI_GREEN))
