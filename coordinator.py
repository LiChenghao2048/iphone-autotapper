#!/usr/bin/env python3
"""
iPhone Action Coordinator
Manages WDA, executes action scripts, and exposes an HTTP control API.

Usage:
    python3 coordinator.py

API (default port 9000):
    POST /load    {"script": "path/to/script.yaml"}  — load and start a script
    POST /pause                                        — pause all actions
    POST /resume                                       — resume all actions
    POST /stop                                         — stop all actions
    GET  /status                                       — current state + stats
"""

import math
import os
import subprocess
import threading
import time

import requests
import yaml
from flask import Flask, jsonify, request

# ── Config ────────────────────────────────────────────────────────────────────
WDA_URL = "http://127.0.0.1:8100"
PORT = 9000
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.logger.disabled = True

# ── Shared state ──────────────────────────────────────────────────────────────
_lock = threading.Lock()
_session_id: str = ""
_pause_event = threading.Event()
_pause_event.set()          # set = running, clear = paused
_user_paused: bool = False  # tracks whether pause was user-initiated
_stop_event = threading.Event()
_action_threads: list = []
_stats: dict = {}           # action index -> {type, count, status}
# ─────────────────────────────────────────────────────────────────────────────


# ── WDA management ────────────────────────────────────────────────────────────

def wda_is_up() -> bool:
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=3)
        return r.json()["value"]["ready"]
    except Exception:
        return False


def start_wda() -> bool:
    print("[coordinator] Starting WDA...")
    subprocess.Popen(
        ["bash", os.path.join(SCRIPT_DIR, "start_wda.sh")],
        stdout=open("/tmp/wda.log", "w"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(45):
        time.sleep(2)
        if wda_is_up():
            print("[coordinator] WDA is live.")
            return True
    print("[coordinator] ERROR: WDA did not come up. Check /tmp/wda.log")
    return False


def ensure_wda():
    if not wda_is_up():
        start_wda()


def get_or_create_session() -> str:
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def wda_monitor():
    """Background thread: restart WDA if it goes down."""
    while not _stop_event.is_set():
        time.sleep(10)
        if _stop_event.is_set():
            break
        if not wda_is_up():
            print("[coordinator] WDA down, restarting...")
            _pause_event.clear()
            if start_wda():
                global _session_id
                try:
                    with _lock:
                        _session_id = get_or_create_session()
                    print(f"[coordinator] New session: {_session_id}")
                except Exception as e:
                    print(f"[coordinator] Failed to create session: {e}")
            if not _user_paused:
                _pause_event.set()


# ── WDA actions ───────────────────────────────────────────────────────────────

def wda_call(fn, max_retries: int = 5):
    """Call a WDA function with automatic session recovery on failure."""
    global _session_id
    for attempt in range(max_retries):
        try:
            return fn(_session_id)
        except Exception as e:
            print(f"[coordinator] WDA call failed ({e.__class__.__name__}), retry {attempt+1}/{max_retries}")
            time.sleep(2)
            try:
                with _lock:
                    _session_id = get_or_create_session()
            except Exception:
                time.sleep(3)


def do_tap(session_id: str, x: int, y: int):
    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer",
            "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 50},
                {"type": "pointerUp", "button": 0},
            ],
        }]},
        timeout=5,
    )


def do_drag(session_id: str, x1: int, y1: int, x2: int, y2: int, duration: int):
    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer",
            "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": duration, "x": x2, "y": y2},
                {"type": "pointerUp", "button": 0},
            ],
        }]},
        timeout=duration / 1000 + 5,
    )


def do_circle(session_id: str, center_x: int, center_y: int, radius: int, duration: int):
    N = 36  # points around the circle (one every 10 degrees)
    step_ms = max(1, duration // N)

    pointer_actions = [
        {"type": "pointerMove", "duration": 0,
         "x": int(center_x + radius), "y": center_y},
        {"type": "pointerDown", "button": 0},
    ]
    for i in range(1, N + 1):
        angle = 2 * math.pi * i / N
        pointer_actions.append({
            "type": "pointerMove",
            "duration": step_ms,
            "x": int(center_x + radius * math.cos(angle)),
            "y": int(center_y + radius * math.sin(angle)),
        })
    pointer_actions.append({"type": "pointerUp", "button": 0})

    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer",
            "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": pointer_actions,
        }]},
        timeout=duration / 1000 + 5,
    )


# ── Action runner ─────────────────────────────────────────────────────────────

def interruptible_sleep(seconds: float):
    """Sleep in small increments, respecting pause and stop events."""
    end = time.time() + seconds
    while time.time() < end:
        if _stop_event.is_set():
            return
        _pause_event.wait()
        time.sleep(min(0.05, max(0, end - time.time())))


def run_action(idx: int, action: dict):
    action_type = action["type"]
    interval = action.get("interval", 1.0)
    max_count = action.get("count", 0)
    count = 0

    _stats[idx] = {"type": action_type, "count": 0, "status": "running"}

    while not _stop_event.is_set():
        if max_count > 0 and count >= max_count:
            break

        _pause_event.wait()
        if _stop_event.is_set():
            break

        if action_type == "tap":
            wda_call(lambda sid, a=action: do_tap(sid, a["x"], a["y"]))
        elif action_type == "drag":
            wda_call(lambda sid, a=action: do_drag(
                sid, a["x1"], a["y1"], a["x2"], a["y2"], a.get("duration", 500)))
        elif action_type == "circle":
            wda_call(lambda sid, a=action: do_circle(
                sid, a["center_x"], a["center_y"], a["radius"], a.get("duration", 1000)))

        count += 1
        _stats[idx]["count"] = count

        if max_count == 0 or count < max_count:
            interruptible_sleep(interval)

    _stats[idx]["status"] = "done"


# ── HTTP API ──────────────────────────────────────────────────────────────────

@app.route("/load", methods=["POST"])
def load():
    global _action_threads, _stats

    # Stop any running actions
    _stop_event.set()
    _pause_event.set()
    for t in _action_threads:
        t.join(timeout=5)
    _stop_event.clear()
    _action_threads = []
    _stats = {}

    script_path = request.json.get("script")
    if not script_path:
        return jsonify({"error": "missing 'script' field"}), 400

    try:
        with open(script_path) as f:
            script = yaml.safe_load(f)
    except FileNotFoundError:
        return jsonify({"error": f"script not found: {script_path}"}), 404

    actions = script.get("actions", [])
    if not actions:
        return jsonify({"error": "script has no actions"}), 400

    for idx, action in enumerate(actions):
        t = threading.Thread(target=run_action, args=(idx, action), daemon=True)
        t.start()
        _action_threads.append(t)

    return jsonify({"status": "started", "actions": len(actions)})


@app.route("/pause", methods=["POST"])
def pause():
    global _user_paused
    _user_paused = True
    _pause_event.clear()
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    global _user_paused
    _user_paused = False
    _pause_event.set()
    return jsonify({"status": "resumed"})


@app.route("/stop", methods=["POST"])
def stop():
    _stop_event.set()
    _pause_event.set()
    return jsonify({"status": "stopped"})


@app.route("/status", methods=["GET"])
def status():
    state = "paused" if not _pause_event.is_set() else "running"
    return jsonify({
        "state": state,
        "wda": "up" if wda_is_up() else "down",
        "session": _session_id,
        "actions": _stats,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _session_id

    print("[coordinator] Ensuring WDA is up...")
    ensure_wda()

    print("[coordinator] Creating WDA session...")
    try:
        _session_id = get_or_create_session()
        print(f"[coordinator] Session: {_session_id}")
    except Exception as e:
        print(f"[coordinator] ERROR: Could not create session: {e}")

    threading.Thread(target=wda_monitor, daemon=True).start()

    print(f"[coordinator] Ready — listening on http://localhost:{PORT}")
    print(f"[coordinator] Load a script with: python3 control.py start script.yaml")
    app.run(host="127.0.0.1", port=PORT, use_reloader=False)


if __name__ == "__main__":
    main()
