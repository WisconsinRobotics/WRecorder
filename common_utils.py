import argparse
import json
import os
import signal
from typing import Any, Callable, Dict, Iterable, List, Optional
import time
import logging


DEFAULTS_FILE_NAME = "argument_defaults.json"
DISCOVERY_MESSAGE_TYPE = "WRECORDER_DISCOVERY"
DISCOVERY_VERSION = 1
DISCOVERY_TEXT_ENCODING = "utf8"
VALID_PORT_MIN = 1
VALID_PORT_MAX = 65535


class LoggingFormatter(logging.Formatter):
	ANSI_RESET = "\033[0m"
	ANSI_RED = "\033[91m"
	ANSI_GREEN = "\033[92m"
	ANSI_YELLOW = "\033[93m"
	ANSI_BOLD = "\033[1m"

	FORMATS = {
		logging.DEBUG: ANSI_YELLOW,
		logging.INFO: ANSI_GREEN,
		logging.WARNING: ANSI_YELLOW,
		logging.ERROR: ANSI_RED,
		logging.CRITICAL: ANSI_RED + ANSI_BOLD,
	}
	formatter = logging.Formatter(
		"%(relativeCreated)dms %(levelname)s [%(name)s]: %(message)s"
	)

	def format(self, record: logging.LogRecord) -> str:
		log_color = self.FORMATS.get(record.levelno, "")
		message = self.formatter.format(record)
		return f"{log_color}{message}{self.ANSI_RESET}"


loggers = {}


def get_logger(name: str) -> logging.Logger:
	if name in loggers:
		return loggers[name]
	logger = logging.getLogger(name)
	logger.setLevel(logging.DEBUG)
	if not logger.hasHandlers():
		handler = logging.StreamHandler()
		handler.setFormatter(LoggingFormatter())
		logger.addHandler(handler)
	loggers[name] = logger
	return logger


def is_valid_port(port: Any) -> bool:
	return isinstance(port, int) and VALID_PORT_MIN <= port <= VALID_PORT_MAX


def are_non_negative_ints(
	values: Iterable[Any], require_non_empty: bool = False
) -> bool:
	saw_any = False
	for value in values:
		saw_any = True
		if not isinstance(value, int) or value < 0:
			return False
	if require_non_empty and not saw_any:
		return False
	return True


def has_valid_sequential_port_range(base_port: Any, stream_count: Any) -> bool:
	if not is_valid_port(base_port):
		return False
	return (base_port + stream_count - 1) <= VALID_PORT_MAX


def build_sequential_ports(base_port: Any, stream_count: Any) -> Optional[List[int]]:
	if not has_valid_sequential_port_range(base_port, stream_count):
		return None
	return [base_port + i for i in range(stream_count)]


def parse_discovery_payload(
	packet: bytes,
	streamer_name_filter: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
	"""Parse and validate a discovery packet.

	Returns a normalized dict for valid packets, otherwise None.
	"""
	try:
		payload = json.loads(packet.decode(DISCOVERY_TEXT_ENCODING))
	except (UnicodeDecodeError, json.JSONDecodeError):
		return None

	if payload.get("type") != DISCOVERY_MESSAGE_TYPE:
		return None
	if payload.get("version") != DISCOVERY_VERSION:
		return None

	streamer_name = str(payload.get("streamer_name", "")).strip()
	streamer_ip = str(payload.get("streamer_ip", "")).strip()
	base_port = payload.get("base_port")
	stream_count = payload.get("stream_count")

	if streamer_name_filter and streamer_name != streamer_name_filter:
		return None

	if not streamer_ip or not streamer_name:
		return None

	if not is_valid_port(base_port):
		return None

	if not isinstance(stream_count, int) or stream_count < 1:
		return None

	return {
		"streamer_name": streamer_name,
		"streamer_ip": streamer_ip,
		"base_port": base_port,
		"stream_count": stream_count,
	}


def install_stop_signal_handlers(stop_callback: Callable[[], None], logger: logging.Logger, message: str):
	"""Install SIGINT/SIGTERM handlers that print a message and request shutdown."""

	def _signal_handler(signum, frame):
		_ = signum
		_ = frame
		logger.info(message)
		stop_callback()

	signal.signal(signal.SIGINT, _signal_handler)
	signal.signal(signal.SIGTERM, _signal_handler)


def apply_required_external_defaults(
	parser: argparse.ArgumentParser,
	section_name: str,
	defaults_file_name: str = DEFAULTS_FILE_NAME,
):
	"""Load strict defaults from JSON and apply to parser.

	Required behavior:
	- File must exist and be valid JSON
	- `both` and role section must be objects
	- No unknown keys for the parser
	- All optional parser args must have defaults provided
	"""
	defaults_path = os.path.join(os.path.dirname(__file__), defaults_file_name)
	try:
		with open(defaults_path, "r", encoding="utf-8") as f:
			payload = json.load(f)
	except FileNotFoundError as exc:
		raise RuntimeError(f"defaults file missing: {defaults_path}") from exc
	except PermissionError as exc:
		raise RuntimeError(f"cannot read defaults file: {defaults_path}") from exc
	except json.JSONDecodeError as exc:
		raise RuntimeError(
			f"invalid JSON in defaults file: {defaults_path}: {exc}"
		) from exc

	both_defaults = payload.get("both", {})
	role_defaults = payload.get(section_name, {})
	if not isinstance(both_defaults, dict) or not isinstance(role_defaults, dict):
		raise RuntimeError(
			"defaults schema must contain object sections: 'both' and role section"
		)

	merged = dict(both_defaults)
	merged.update(role_defaults)

	valid_dests = {action.dest for action in parser._actions if action.dest != "help"}
	unknown_keys = sorted(set(merged.keys()) - valid_dests)
	if unknown_keys:
		raise RuntimeError(f"unknown defaults keys for {section_name}: {unknown_keys}")

	required_dests = [
		action.dest
		for action in parser._actions
		if action.option_strings and action.dest != "help"
	]
	missing_keys = sorted(dest for dest in required_dests if dest not in merged)
	if missing_keys:
		raise RuntimeError(f"missing defaults keys for {section_name}: {missing_keys}")

	parser.set_defaults(**merged)
	get_logger(__name__).info(
		f"loaded {len(merged)} defaults for section '{section_name}' from {defaults_file_name}"
	)


def int_in_range(name: str, minimum: int, maximum: int = None):
	def _validator(value: str) -> int:
		try:
			parsed = int(value)
		except ValueError as exc:
			raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
		if parsed < minimum:
			upper = f" and <= {maximum}" if maximum is not None else ""
			raise argparse.ArgumentTypeError(f"{name} must be >= {minimum}{upper}")
		if maximum is not None and parsed > maximum:
			raise argparse.ArgumentTypeError(f"{name} must be <= {maximum}")
		return parsed

	return _validator

def clamp[T](value: T, minimum: T, maximum: T) -> T: # generics in python lmao
	return max(minimum, min(maximum, value))
