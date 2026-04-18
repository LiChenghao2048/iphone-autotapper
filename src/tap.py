#!/usr/bin/env python3
"""
iPhone Auto-Tapper
Repeatedly taps one or more screen coordinates on a connected iPhone via WDA HTTP API.

Usage:
    python3 tap.py --coords "X,Y" ["X,Y" ...]  [--interval SECS] [--count N]
    python3 tap.py [--x X] [--y Y]             [--interval SECS] [--count N]

Defaults:
    --coords       if omitted, falls back to --x/--y
    --x 215        horizontal coordinate (points)  [single-coord fallback]
    --y 466        vertical coordinate (points)    [single-coord fallback]
    --interval 1.0 seconds between cycles (all coords tapped, then sleep)
    --count 0      0 = cycle forever until Ctrl+C

Controls:
    Space / p      pause / resume
    Ctrl+C         quit
"""

import argparse
import select
import sys
import termios
import time
import tty
import requests

# ── Config ───────────────────────────────────────────────────────────────────
WDA_URL = "http://127.0.0.1:8100"   # WDA forwarded via iproxy
# ────────────────────────────────────────────────────────────────────────────

_session_id: str = ""


def get_or_create_session() -> str:
    """Create a new WDA session (works on whatever app is on screen)."""
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def tap(session_id: str, x: int, y: int) -> None:
    """Perform a single tap. Raises RuntimeError if WDA rejects the session."""
    r = requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={
            "actions": [{
                "type": "pointer",
                "id": "finger",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": 50},
                    {"type": "pointerUp", "button": 0},
                ],
            }]
        },
        timeout=5,
    )
    if r.status_code != 200:
        raise RuntimeError(f"WDA rejected tap (HTTP {r.status_code}) — session stolen?")


def tap_with_retry(x: int, y: int) -> None:
    """Tap, reclaiming the WDA session whenever it has been stolen."""
    global _session_id
    attempt = 0
    while True:
        try:
            tap(_session_id, x, y)
            return
        except Exception as e:
            attempt += 1
            print(f"\n  [warn] tap failed ({e.__class__.__name__}), reclaiming session (attempt {attempt})...")
            if attempt % 10 == 0:
                print(f"  [warn] still retrying after {attempt} attempts — is WDA up?", file=sys.stderr)
            time.sleep(1)
            try:
                _session_id = get_or_create_session()
                print(f"  [info] reclaimed session: {_session_id}")
            except Exception:
                time.sleep(2)


def check_keypress() -> str:
    """Return a pressed key if one is waiting, otherwise empty string."""
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return ""


def parse_coords(args) -> list:
    """Return list of (x, y) tuples from --coords, or fall back to --x/--y."""
    if args.coords:
        result = []
        for pair in args.coords:
            parts = pair.split(",")
            if len(parts) != 2:
                raise ValueError(f"Invalid coord '{pair}': expected 'X,Y'")
            try:
                result.append((int(parts[0]), int(parts[1])))
            except ValueError:
                raise ValueError(f"Invalid coord '{pair}': expected integer values")
        return result
    return [(args.x, args.y)]


def main():
    parser = argparse.ArgumentParser(description="Auto-tap one or more iPhone screen coordinates.")
    parser.add_argument("--coords",   nargs="+",  metavar="X,Y",
                        help='Coordinates to tap in sequence, e.g. "700,400" "335,250"')
    parser.add_argument("--x",        type=int,   default=215,  help=argparse.SUPPRESS)
    parser.add_argument("--y",        type=int,   default=466,  help=argparse.SUPPRESS)
    parser.add_argument("--interval", type=float, default=1.0,  help="Seconds between cycles (default 1.0)")
    parser.add_argument("--count",    type=int,   default=0,    help="Number of cycles; 0 = infinite (default 0)")
    args = parser.parse_args()

    try:
        coords = parse_coords(args)
    except ValueError as e:
        parser.error(str(e))

    print(f"Connecting to WDA at {WDA_URL} ...")
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=5)
        assert r.json()["value"]["ready"], "WDA not ready"
    except Exception as e:
        print(f"ERROR: WDA not reachable: {e}", file=sys.stderr)
        print("Make sure start_wda.sh is running first.", file=sys.stderr)
        sys.exit(1)

    global _session_id
    _session_id = get_or_create_session()
    coords_str = "  ".join(f"({x},{y})" for x, y in coords)
    print(f"Session: {_session_id}")
    print(f"Coords : {coords_str}  interval={args.interval}s  count={'∞' if args.count == 0 else args.count}")
    print("Tapping — press Space/p to pause, Ctrl+C to stop.\n")

    # Put terminal in raw mode so keypresses are read instantly (no Enter needed)
    old_term = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin)

    paused = False
    cycle = 0
    tapped = 0
    try:
        while args.count == 0 or cycle < args.count:
            key = check_keypress()
            if key in (" ", "p"):
                paused = not paused
                if paused:
                    print("\n  [paused]  press Space/p to resume", flush=True)
                else:
                    print("  [resumed]", flush=True)

            if paused:
                time.sleep(0.1)
                continue

            for x, y in coords:
                tap_with_retry(x, y)
                tapped += 1
                print(f"  tap #{tapped}  ({x}, {y})", end="\r", flush=True)

            cycle += 1
            if args.count == 0 or cycle < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n\nStopped after {tapped} tap(s) across {cycle} cycle(s).")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_term)


if __name__ == "__main__":
    main()
