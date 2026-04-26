#!/usr/bin/env python3
"""
Sequence runner — executes a mixed list of gestures loaded from a YAML preset.

Usage:
    python3 src/sequence.py --preset brawl_stars [--count N] [--interval SECS]

Preset files live in src/presets/<name>.yaml.
Each entry must have a "type" field: tap | swipe | wait.

    tap:   x, y
    swipe: x1, y1, x2, y2, duration_ms (default 500)
    wait:  ms

Controls:
    Space / p    pause / resume
    Ctrl+C       quit
"""

import argparse
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import yaml

_src = str(Path(__file__).resolve().parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

from _session import WDA_URL, get_or_create_session  # noqa: E402
from gestures.tap import tap                          # noqa: E402
from gestures.swipe import swipe                      # noqa: E402

import requests

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

_pause_event = threading.Event()


@dataclass
class Tap:
    x: int
    y: int


@dataclass
class Swipe:
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 500


@dataclass
class Wait:
    ms: int


Step = Union[Tap, Swipe, Wait]


def load_preset(name: str) -> list[Step]:
    """Load and parse a YAML preset file from src/presets/<name>.yaml."""
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Preset not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"Preset '{name}' must be a YAML list of steps")
    steps = []
    for i, entry in enumerate(raw):
        kind = entry.get("type")
        if kind == "tap":
            steps.append(Tap(x=int(entry["x"]), y=int(entry["y"])))
        elif kind == "swipe":
            steps.append(Swipe(
                x1=int(entry["x1"]), y1=int(entry["y1"]),
                x2=int(entry["x2"]), y2=int(entry["y2"]),
                duration_ms=int(entry.get("duration_ms", 500)),
            ))
        elif kind == "wait":
            steps.append(Wait(ms=int(entry["ms"])))
        else:
            raise ValueError(f"Step {i}: unknown type '{kind}' (expected tap, swipe, wait)")
    return steps


def run_sequence(steps: list[Step], session_id: str, count: int = 0) -> None:
    """Execute steps in order, repeating count times (0 = forever).

    Raises ValueError if steps is empty.
    Honours the module-level _pause_event: waits while paused.
    Propagates exceptions from tap/swipe/wait immediately.
    """
    if not steps:
        raise ValueError("steps must not be empty")
    cycle = 0
    while count == 0 or cycle < count:
        for step in steps:
            while _pause_event.is_set():
                time.sleep(0.05)
            if isinstance(step, Tap):
                tap(session_id, step.x, step.y)
            elif isinstance(step, Swipe):
                swipe(session_id, step.x1, step.y1, step.x2, step.y2, step.duration_ms)
            elif isinstance(step, Wait):
                _interruptible_sleep(step.ms / 1000)
        cycle += 1


def _interruptible_sleep(secs: float) -> None:
    """Sleep for secs, waking every 50 ms to check the pause event."""
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(0.05, remaining))
        while _pause_event.is_set():
            time.sleep(0.05)


def _keyboard_listener() -> None:
    while True:
        ch = sys.stdin.read(1)
        if not ch:
            break
        if ch in (" ", "p"):
            if _pause_event.is_set():
                _pause_event.clear()
                print("  [resumed]", flush=True)
            else:
                _pause_event.set()
                print("\n  [paused]  press Space/p to resume", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run a gesture sequence from a YAML preset.")
    parser.add_argument("--preset",   required=True, help="Preset name (file in src/presets/)")
    parser.add_argument("--count",    type=int, default=0, help="Repetitions; 0 = loop forever (default 0)")
    args = parser.parse_args()

    try:
        steps = load_preset(args.preset)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to WDA at {WDA_URL} ...")
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=5)
        if not r.json()["value"]["ready"]:
            raise RuntimeError("WDA not ready")
    except Exception as e:
        print(f"ERROR: WDA not reachable: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = get_or_create_session()
    print(f"Session : {session_id}")
    print(f"Preset  : {args.preset}  ({len(steps)} steps)")
    print(f"Count   : {'∞' if args.count == 0 else args.count}")
    print("Running — press Space/p to pause, Ctrl+C to stop.\n")

    old_term = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin)
    try:
        _pause_event.clear()
        threading.Thread(target=_keyboard_listener, daemon=True).start()
        run_sequence(steps, session_id, args.count)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_term)


if __name__ == "__main__":
    main()
