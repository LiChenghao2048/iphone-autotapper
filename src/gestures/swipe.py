#!/usr/bin/env python3
"""
iPhone Swipe
Performs a single swipe gesture on a connected iPhone via WDA HTTP API.

Usage:
    python3 src/gestures/swipe.py --x1 X --y1 Y --x2 X --y2 Y [--duration MS]

Defaults:
    --duration 500   milliseconds for the swipe motion
"""

import argparse
import sys
import requests

# ── Config ────────────────────────────────────────────────────────────────────
WDA_URL = "http://127.0.0.1:8100"   # WDA forwarded via iproxy
# ─────────────────────────────────────────────────────────────────────────────


def get_or_create_session() -> str:
    """Create a new WDA session (works on whatever app is on screen)."""
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def swipe(session_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
    """Swipe from (x1, y1) to (x2, y2) over duration_ms milliseconds.

    Raises ValueError for non-positive duration_ms.
    Raises RuntimeError if WDA rejects the request or a network error occurs.
    The HTTP timeout is set to duration_ms + 2s so it always outlasts the gesture.
    """
    if duration_ms <= 0:
        raise ValueError(f"duration_ms must be positive, got {duration_ms}")
    r = requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={
            "actions": [{
                "type": "pointer",
                "id": "finger",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0,           "x": x1, "y": y1},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerMove", "duration": duration_ms, "x": x2, "y": y2},
                    {"type": "pointerUp",   "button": 0},
                ],
            }]
        },
        timeout=max(5, duration_ms / 1000 + 2),
    )
    if r.status_code != 200:
        raise RuntimeError(f"WDA rejected swipe (HTTP {r.status_code})")


def _swipe_or_exit(session_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
    """Call swipe(), printing errors and exiting on failure."""
    try:
        swipe(session_id, x1, y1, x2, y2, duration_ms)
    except (RuntimeError, requests.RequestException) as e:
        print(f"ERROR: swipe failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Swipe on iPhone screen via WDA.")
    parser.add_argument("--x1",       type=int, required=True, help="Start X coordinate (points)")
    parser.add_argument("--y1",       type=int, required=True, help="Start Y coordinate (points)")
    parser.add_argument("--x2",       type=int, required=True, help="End X coordinate (points)")
    parser.add_argument("--y2",       type=int, required=True, help="End Y coordinate (points)")
    parser.add_argument("--duration", type=int, default=500,   help="Swipe duration in ms (default 500)")
    args = parser.parse_args()

    print(f"Connecting to WDA at {WDA_URL} ...")
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=5)
        assert r.json()["value"]["ready"], "WDA not ready"
    except Exception as e:
        print(f"ERROR: WDA not reachable: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = get_or_create_session()
    print(f"Session: {session_id}")
    print(f"Swiping ({args.x1},{args.y1}) → ({args.x2},{args.y2}) over {args.duration}ms")

    _swipe_or_exit(session_id, args.x1, args.y1, args.x2, args.y2, args.duration)
    print("Done.")


if __name__ == "__main__":
    main()
