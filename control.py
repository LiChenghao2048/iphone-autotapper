#!/usr/bin/env python3
"""
Coordinator control CLI.

Usage:
    python3 control.py start <script.yaml>   — load and run a script
    python3 control.py pause                  — pause all actions
    python3 control.py resume                 — resume all actions
    python3 control.py stop                   — stop all actions
    python3 control.py status                 — show current state
"""

import json
import os
import sys

import requests

COORDINATOR_URL = "http://127.0.0.1:9000"


def print_response(r):
    print(json.dumps(r.json(), indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    try:
        if cmd == "start":
            if len(sys.argv) < 3:
                print("Usage: python3 control.py start <script.yaml>")
                sys.exit(1)
            script_path = os.path.abspath(sys.argv[2])
            print_response(requests.post(
                f"{COORDINATOR_URL}/load",
                json={"script": script_path},
            ))

        elif cmd == "pause":
            print_response(requests.post(f"{COORDINATOR_URL}/pause"))

        elif cmd == "resume":
            print_response(requests.post(f"{COORDINATOR_URL}/resume"))

        elif cmd == "stop":
            print_response(requests.post(f"{COORDINATOR_URL}/stop"))

        elif cmd == "status":
            print_response(requests.get(f"{COORDINATOR_URL}/status"))

        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print("ERROR: coordinator not running. Start it with: python3 coordinator.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
