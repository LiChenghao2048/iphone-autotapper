#!/usr/bin/env python3
"""
iPhone Auto-Tapper
Repeatedly taps a fixed screen coordinate on a connected iPhone via WDA HTTP API.

Usage:
    python3 tap.py [--x X] [--y Y] [--interval SECS] [--count N]

Defaults:
    --x 215        horizontal coordinate (points)
    --y 466        vertical coordinate (points)
    --interval 1.0 seconds between taps
    --count 0      0 = tap forever until Ctrl+C
"""

import argparse
import time
import sys
import requests

# ── Config ───────────────────────────────────────────────────────────────────
WDA_URL = "http://127.0.0.1:8100"   # WDA forwarded via iproxy
# ────────────────────────────────────────────────────────────────────────────


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
    """Perform a single tap at absolute screen coordinates (points)."""
    requests.post(
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


def main():
    parser = argparse.ArgumentParser(description="Auto-tap a fixed iPhone screen coordinate.")
    parser.add_argument("--x",        type=int,   default=215,  help="X coordinate in points (default 215)")
    parser.add_argument("--y",        type=int,   default=466,  help="Y coordinate in points (default 466)")
    parser.add_argument("--interval", type=float, default=1.0,  help="Seconds between taps (default 1.0)")
    parser.add_argument("--count",    type=int,   default=0,    help="Number of taps; 0 = infinite (default 0)")
    args = parser.parse_args()

    print(f"Connecting to WDA at {WDA_URL} ...")
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=5)
        assert r.json()["value"]["ready"], "WDA not ready"
    except Exception as e:
        print(f"ERROR: WDA not reachable: {e}", file=sys.stderr)
        print("Make sure start_wda.sh is running first.", file=sys.stderr)
        sys.exit(1)

    sid = get_or_create_session()
    print(f"Session: {sid}")
    print(f"Target : ({args.x}, {args.y})  interval={args.interval}s  count={'∞' if args.count == 0 else args.count}")
    print("Tapping — press Ctrl+C to stop.\n")

    tapped = 0
    try:
        while args.count == 0 or tapped < args.count:
            tap(sid, args.x, args.y)
            tapped += 1
            print(f"  tap #{tapped}  ({args.x}, {args.y})", end="\r", flush=True)
            if args.count == 0 or tapped < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n\nStopped after {tapped} tap(s).")


if __name__ == "__main__":
    main()
