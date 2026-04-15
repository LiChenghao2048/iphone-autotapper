#!/usr/bin/env python3
"""
Coordinate Finder — saves a screenshot of the iPhone's current screen.
Open the saved PNG in Preview; the bottom status bar shows (x, y) as you
hover. Divide both numbers by 3 to get the tap coordinate for tap.py.

Usage:
    python3 find_coords.py               # saves to screenshot.png
    python3 find_coords.py --out my.png  # custom filename
    python3 find_coords.py --open        # auto-opens in Preview
"""

import argparse
import base64
import subprocess
import sys
import requests

WDA_URL = "http://127.0.0.1:8100"
SCALE   = 3   # iPhone 14 Pro Max is a 3× device (1290×2796 px → 430×932 pts)


def get_session() -> str:
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def take_screenshot(session_id: str) -> bytes:
    r = requests.get(f"{WDA_URL}/session/{session_id}/screenshot", timeout=10)
    b64 = r.json()["value"]
    return base64.b64decode(b64)


def main():
    parser = argparse.ArgumentParser(description="Screenshot iPhone screen for coordinate lookup.")
    parser.add_argument("--out",  default="screenshot.png", help="Output PNG filename")
    parser.add_argument("--open", action="store_true",      help="Open in Preview after saving")
    args = parser.parse_args()

    try:
        requests.get(f"{WDA_URL}/status", timeout=3)
    except Exception:
        print("ERROR: WDA not reachable. Run ./start_wda.sh first.", file=sys.stderr)
        sys.exit(1)

    print("Taking screenshot...")
    sid  = get_session()
    data = take_screenshot(sid)

    with open(args.out, "wb") as f:
        f.write(data)

    print(f"Saved: {args.out}  ({len(data)//1024} KB)")
    print()
    print("How to read coordinates:")
    print(f"  1. Open {args.out} in Preview (or any image viewer).")
    print(f"  2. Hover over your target point — note the pixel (x, y).")
    print(f"  3. Divide both by {SCALE}  →  that's your --x and --y for tap.py.")
    print()
    print(f"  Example: pixel (645, 1398)  →  tap.py --x 215 --y 466")
    print(f"  Screen size: 1290×2796 px  =  430×932 pts")

    if args.open:
        subprocess.run(["open", args.out])


if __name__ == "__main__":
    main()
