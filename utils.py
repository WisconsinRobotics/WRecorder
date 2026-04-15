import argparse
import json
import os
import signal
from typing import Callable


DEFAULTS_FILE_NAME = "argument_defaults.json"
DISCOVERY_MESSAGE_TYPE = "WRECORDER_DISCOVERY"
DISCOVERY_VERSION = 1


def install_stop_signal_handlers(stop_callback: Callable[[], None], message: str):
    """Install SIGINT/SIGTERM handlers that print a message and request shutdown."""

    def _signal_handler(signum, frame):
        _ = signum
        _ = frame
        print(message)
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
        raise RuntimeError(f"invalid JSON in defaults file: {defaults_path}: {exc}") from exc

    both_defaults = payload.get("both", {})
    role_defaults = payload.get(section_name, {})
    if not isinstance(both_defaults, dict) or not isinstance(role_defaults, dict):
        raise RuntimeError("defaults schema must contain object sections: 'both' and role section")

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
    print(f"\033[92m[defaults] loaded {len(merged)} defaults from {defaults_file_name}\033[0m")
