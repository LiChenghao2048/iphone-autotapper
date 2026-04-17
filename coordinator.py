#!/usr/bin/env python3
"""
iPhone Action Coordinator
Manages WDA, executes action scripts, and exposes an HTTP control API.

Architecture:
  - One scheduler thread per action: watches the clock, pushes to queue when
    interval elapses. Skips the cycle if a slot for that action is already
    queued. Clock freezes while paused.
  - One executor thread: pulls from queue one at a time, executes against WDA.
    Owns the single WDA session and handles recovery automatically.

Usage:
    python3 coordinator.py

API (default port 9000):
    POST /load    {"script": "path/to/script.yaml"}  — load and start a script
    POST /pause                                        — freeze all schedulers
    POST /resume                                       — resume all schedulers
    POST /stop                                         — stop all actions
    GET  /status                                       — current state + stats
    GET  /screenshot                                   — capture phone screen
"""

import math
import os
import queue
import random
import subprocess
import threading
import time

import requests
import yaml
from flask import Flask, jsonify, request

# ── Config ────────────────────────────────────────────────────────────────────
WDA_URL    = "http://127.0.0.1:8100"
PORT       = 9000
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.logger.disabled = True

# ── Shared state ──────────────────────────────────────────────────────────────
_pause_event  = threading.Event()
_pause_event.set()          # set = running, clear = paused
_user_paused  = False
_stop_event   = threading.Event()

_action_queue  = queue.Queue()
_pending       = set()       # action indices currently in the queue
_pending_lock  = threading.Lock()
_stats         = {}          # action index → {type, scheduled, status}
_executing     = None        # action index currently being executed
_executor_busy = False       # True while executor is running an action

_scheduler_threads: list = []
_executor_thread         = None

_session_id   = ""
_session_lock = threading.Lock()
_wda_ready    = threading.Event()   # set when a valid WDA session exists
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


def get_session() -> str:
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def session_is_valid() -> bool:
    """Check if the current session is still alive."""
    if not _session_id:
        return False
    try:
        r = requests.get(f"{WDA_URL}/session/{_session_id}", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def ensure_session():
    """Ensure a valid WDA session exists. Skips if current session is still alive."""
    global _session_id
    if session_is_valid():
        _wda_ready.set()
        return
    _wda_ready.clear()
    while True:
        try:
            if not wda_is_up():
                start_wda()
            with _session_lock:
                _session_id = get_session()
            _wda_ready.set()
            print(f"[coordinator] Session ready: {_session_id}")
            return
        except Exception as e:
            print(f"[coordinator] Session setup failed ({e}), retrying in 5s...")
            time.sleep(5)


def wda_monitor():
    """Background thread: detect WDA going down and recover automatically."""
    while True:
        time.sleep(10)
        # Skip check while executor is actively running — WDA must be up
        if _executor_busy:
            continue
        if not wda_is_up():
            # Retry a few times before declaring WDA down
            for _ in range(3):
                time.sleep(2)
                if _executor_busy or wda_is_up():
                    break
            else:
                print("[coordinator] WDA down, recovering...")
                ensure_session()
                if not _user_paused:
                    _pause_event.set()


# ── Parameter resolver ────────────────────────────────────────────────────────

def resolve(value):
    """Return value as-is, or pick a random number if value is a [min, max] range."""
    if isinstance(value, list):
        if isinstance(value[0], float) or isinstance(value[1], float):
            return random.uniform(value[0], value[1])
        return random.randint(value[0], value[1])
    return value


# ── WDA actions ───────────────────────────────────────────────────────────────

def do_tap(session_id: str, x: int, y: int):
    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer", "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause",       "duration": 50},
                {"type": "pointerUp",   "button": 0},
            ],
        }]},
        timeout=5,
    )


def do_drag(session_id: str, x1: int, y1: int, x2: int, y2: int, duration: int):
    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer", "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0,        "x": x1, "y": y1},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": duration, "x": x2, "y": y2},
                {"type": "pointerUp",   "button": 0},
            ],
        }]},
        timeout=duration / 1000 + 5,
    )


def do_circle(session_id: str, center_x: int, center_y: int, radius: int, duration: int):
    N       = 36
    step_ms = max(1, duration // N)
    actions = [
        {"type": "pointerMove", "duration": 0,
         "x": int(center_x + radius), "y": center_y},
        {"type": "pointerDown", "button": 0},
    ]
    for i in range(1, N + 1):
        angle = 2 * math.pi * i / N
        actions.append({
            "type": "pointerMove", "duration": step_ms,
            "x": int(center_x + radius * math.cos(angle)),
            "y": int(center_y + radius * math.sin(angle)),
        })
    actions.append({"type": "pointerUp", "button": 0})
    requests.post(
        f"{WDA_URL}/session/{session_id}/actions",
        json={"actions": [{
            "type": "pointer", "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": actions,
        }]},
        timeout=duration / 1000 + 5,
    )


def execute_single(action: dict, sid: str):
    """Execute one instance of an action (fresh random values each call)."""
    t = action["type"]
    if t == "tap":
        do_tap(sid, resolve(action["x"]), resolve(action["y"]))
    elif t == "drag":
        do_drag(sid,
                resolve(action["x1"]), resolve(action["y1"]),
                resolve(action["x2"]), resolve(action["y2"]),
                resolve(action.get("duration", 500)))
    elif t == "circle":
        do_circle(sid,
                  resolve(action["center_x"]), resolve(action["center_y"]),
                  resolve(action["radius"]),
                  resolve(action.get("duration", 1000)))


def execute_action(action: dict):
    """Execute one action (or a burst) against WDA, with session recovery."""
    global _session_id
    burst = action.get("burst", 1)
    count = resolve(burst) if isinstance(burst, list) else burst

    for attempt in range(5):
        try:
            _wda_ready.wait()
            with _session_lock:
                sid = _session_id
            for _ in range(count):
                execute_single(action, sid)
            return
        except Exception as e:
            print(f"[executor] Failed ({e.__class__.__name__}), retry {attempt+1}/5")
            time.sleep(2)
            try:
                with _session_lock:
                    _session_id = get_session()
            except Exception:
                time.sleep(3)


# ── Freezable sleep ───────────────────────────────────────────────────────────

def freezable_sleep(seconds: float):
    """Sleep for `seconds`. Clock freezes while paused; stops early on stop."""
    remaining = seconds
    last      = time.time()
    while remaining > 0:
        if _stop_event.is_set():
            return
        _pause_event.wait()         # blocks while paused; time does not count down
        now      = time.time()
        remaining -= now - last     # only subtract time spent running
        last     = now
        time.sleep(min(0.05, max(0, remaining)))


# ── Scheduler (one per action) ────────────────────────────────────────────────

def scheduler(idx: int, action: dict):
    _stats[idx] = {"type": action["type"], "scheduled": 0, "status": "running"}

    while not _stop_event.is_set():
        _pause_event.wait()
        if _stop_event.is_set():
            break

        with _pending_lock:
            if idx not in _pending:
                _pending.add(idx)
                _action_queue.put((idx, action))
                _stats[idx]["scheduled"] += 1

        freezable_sleep(resolve(action.get("interval", 1.0)))

    _stats[idx]["status"] = "stopped"


# ── Executor (single thread) ──────────────────────────────────────────────────

def executor():
    global _executing, _executor_busy
    while not _stop_event.is_set():
        _pause_event.wait()
        if _stop_event.is_set():
            break
        try:
            idx, action = _action_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        with _pending_lock:
            _pending.discard(idx)
        _executing = idx
        _executor_busy = True
        execute_action(action)
        _executor_busy = False
        _executing = None


# ── HTTP API ──────────────────────────────────────────────────────────────────

@app.route("/load", methods=["POST"])
def load():
    global _scheduler_threads, _executor_thread, _stats, _executing, _executor_busy

    # Stop existing threads
    _stop_event.set()
    _pause_event.set()
    if _executor_thread:
        _executor_thread.join(timeout=5)
    for t in _scheduler_threads:
        t.join(timeout=5)
    _stop_event.clear()

    # Drain queue and pending set
    while not _action_queue.empty():
        try:
            _action_queue.get_nowait()
        except queue.Empty:
            break
    with _pending_lock:
        _pending.clear()
    _stats = {}
    _executing = None
    _executor_busy = False
    _scheduler_threads = []

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

    _executor_thread = threading.Thread(target=executor, daemon=True)
    _executor_thread.start()

    for idx, action in enumerate(actions):
        t = threading.Thread(target=scheduler, args=(idx, action), daemon=True)
        t.start()
        _scheduler_threads.append(t)

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
    current = None
    if _executing is not None:
        a = _stats.get(_executing, {})
        current = {"index": _executing, "type": a.get("type")}
    return jsonify({
        "state":   state,
        "wda":     "up" if wda_is_up() else "down",
        "session": _session_id,
        "current": current,
        "actions": _stats,
    })


@app.route("/screenshot", methods=["GET"])
def screenshot():
    try:
        _wda_ready.wait(timeout=5)
        with _session_lock:
            sid = _session_id
        r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=10)
        return jsonify({"value": r.json()["value"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("[coordinator] Ensuring WDA is up...")
    ensure_session()

    threading.Thread(target=wda_monitor, daemon=True).start()

    print(f"[coordinator] Ready — listening on http://localhost:{PORT}")
    print(f"[coordinator] Load a script with: python3 control.py start script.yaml")
    app.run(host="127.0.0.1", port=PORT, use_reloader=False)


if __name__ == "__main__":
    main()
