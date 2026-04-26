#!/usr/bin/env python3
"""
Screenshot capture for iPhone via WDA.

Usage:
    python3 src/screenshot.py [--save FILE]

Defaults:
    --save   if omitted, prints screenshot size and exits
"""

import argparse
import base64
import sys
import time
import requests

from _session import WDA_URL, get_or_create_session


def take_screenshot() -> bytes:
    """Return raw PNG bytes from the iPhone screen."""
    sid = get_or_create_session()
    r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=10)
    return base64.b64decode(r.json()["value"])


def capture_loop(interval_ms: int, callback) -> None:
    """Capture screenshots at interval_ms, passing raw PNG bytes to callback.

    Runs until interrupted (Ctrl+C) or callback raises an exception.
    """
    interval_s = interval_ms / 1000
    while True:
        img_bytes = take_screenshot()
        callback(img_bytes)
        time.sleep(interval_s)


def main():
    parser = argparse.ArgumentParser(description="Take a screenshot from the connected iPhone.")
    parser.add_argument("--save", metavar="FILE", default=None,
                        help="Save screenshot to FILE (PNG); prints size if omitted")
    args = parser.parse_args()

    try:
        r = requests.get(f"{WDA_URL}/status", timeout=5)
        assert r.json()["value"]["ready"], "WDA not ready"
    except Exception as e:
        print(f"ERROR: WDA not reachable: {e}", file=sys.stderr)
        sys.exit(1)

    img_bytes = take_screenshot()

    if args.save:
        with open(args.save, "wb") as f:
            f.write(img_bytes)
        print(f"Saved: {args.save}  ({len(img_bytes) // 1024} KB)")
    else:
        print(f"Screenshot taken  ({len(img_bytes) // 1024} KB)")


if __name__ == "__main__":
    main()
