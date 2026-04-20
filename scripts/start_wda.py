#!/usr/bin/env python3
"""
Start WebDriverAgent on the connected iPhone and forward port 8100.
Run this once before tap.py. Keep it running in its own terminal.

Usage:
    python3 scripts/start_wda.py

Reads UDID and TEAM from .env in the project root.
"""

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time

import requests

WDA_URL = "http://127.0.0.1:8100"
XCTESTRUN_PATH = pathlib.Path("/tmp/WebDriverAgentRunner.xctestrun")
POLL_INTERVAL = 2   # seconds between WDA readiness checks
POLL_TIMEOUT  = 80  # seconds before giving up
LOG_PATH      = "/tmp/wda.log"
LOG_CAP_BYTES = 2_097_152  # 2 MB — enough to capture startup; discards the rest


# ── Config ─────────────────────────────────────────────────────────────────────

def load_env(env_path: pathlib.Path) -> dict:
    """Parse a .env file and return a dict of key→value pairs.

    Raises FileNotFoundError if the file is missing, ValueError if UDID or
    TEAM are absent.
    """
    if not env_path.exists():
        raise FileNotFoundError(
            f".env not found at {env_path}. "
            "Copy .env.example to .env and fill in your values."
        )
    env: dict = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        # strip inline comments (e.g. UDID=abc # note) to match shell `source` behaviour
        if " #" in value:
            value = value[:value.index(" #")].rstrip()
        env[key.strip()] = value
    for key in ("UDID", "TEAM"):
        if not env.get(key):
            raise ValueError(f"Missing required variable {key!r} in {env_path}")
    return env


# ── xctestrun ──────────────────────────────────────────────────────────────────

def build_xctestrun(team: str, path: pathlib.Path) -> None:
    """Write the xctestrun plist required by xcodebuild test-without-building."""
    path.write_text(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>WebDriverAgentRunner</key>
  <dict>
    <key>IsUITestBundle</key><true/>
    <key>IsXCTRunnerHostedTestBundle</key><true/>
    <key>ProductModuleName</key><string>WebDriverAgentRunner</string>
    <key>SystemAttachmentLifetime</key><string>deleteOnSuccess</string>
    <key>TestBundleDestinationRelativePath</key>
    <string>PlugIns/WebDriverAgentRunner.xctest</string>
    <key>TestHostBundleIdentifier</key>
    <string>com.{team}.WebDriverAgentRunner.xctrunner</string>
    <key>UITargetAppBundleIdentifier</key>
    <string>com.{team}.WebDriverAgentRunner.xctrunner</string>
    <key>UseDestinationArtifacts</key><true/>
    <key>UserAttachmentLifetime</key><string>deleteOnSuccess</string>
  </dict>
  <key>__xctestrun_metadata__</key>
  <dict>
    <key>FormatVersion</key><integer>1</integer>
  </dict>
</dict>
</plist>
""")


# ── Keychain ───────────────────────────────────────────────────────────────────

# ── DerivedData cleanup ────────────────────────────────────────────────────────

def cleanup_derived_data(derived_data: pathlib.Path = None) -> None:
    """Delete stale xcodebuild temporary-* folders left by previous WDA sessions.

    Each `xcodebuild test-without-building` run creates a temporary-* folder in
    Xcode's DerivedData directory. Inside is an xcresult bundle whose
    Session-WebDriverAgentRunner-*.log grows for the entire session — a few
    hours of tapping produces gigabytes. These folders are never cleaned up
    automatically, so we purge them at startup before creating a new one.

    Args:
        derived_data: override the DerivedData path (used in tests).
    """
    if derived_data is None:
        derived_data = pathlib.Path.home() / "Library/Developer/Xcode/DerivedData"
    removed = []
    for folder in derived_data.glob("temporary-*"):
        try:
            shutil.rmtree(folder)
            removed.append(folder.name)
        except Exception as e:
            print(f"[warn] Could not remove {folder.name}: {e}", file=sys.stderr)
    if removed:
        print(f"Cleaned up {len(removed)} stale DerivedData folder(s).")


# ── Logging ────────────────────────────────────────────────────────────────────

def _drain_to_log(pipe, log_path: str, cap_bytes: int = LOG_CAP_BYTES) -> None:
    """Read from pipe; write the first cap_bytes to log_path, then discard.

    Runs in a daemon thread. Captures enough output to debug startup failures
    without letting the log grow unbounded over a long session.

    Opens with line buffering (buffering=1) so each line is flushed to disk
    immediately — safe to read even if the process is killed.
    """
    written = 0
    with open(log_path, "w", buffering=1) as f:
        for line in pipe:
            if written < cap_bytes:
                f.write(line)
                written += len(line)


# ── WDA readiness ──────────────────────────────────────────────────────────────

def wait_for_wda(
    url: str = WDA_URL,
    timeout: float = POLL_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> bool:
    """Poll WDA /status until it responds 200 or timeout is reached.

    Returns True if WDA came up, False if timed out.
    """
    deadline = time.monotonic() + timeout
    print("Waiting for WDA", end="", flush=True)
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{url}/status", timeout=1)
            if r.status_code == 200:
                print(" ready!")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(interval)
    print()
    return False


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    project_root = pathlib.Path(__file__).parent.parent
    env = load_env(project_root / ".env")
    udid, team = env["UDID"], env["TEAM"]

    build_xctestrun(team, XCTESTRUN_PATH)

    print(f"Starting WebDriverAgent on device {udid} ...")
    print("(First run after a 7-day expiry will take ~60s to rebuild)")

    subprocess.run(["pkill", "-u", str(os.getuid()), "-f", "iproxy 8100"], capture_output=True)
    time.sleep(1)

    cleanup_derived_data()

    print("Starting iproxy port forward (8100)...")
    iproxy = subprocess.Popen(
        ["iproxy", "8100", "8100"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)

    wda = subprocess.Popen(
        [
            "xcodebuild", "test-without-building",
            "-xctestrun", str(XCTESTRUN_PATH),
            "-destination", f"id={udid}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_thread = threading.Thread(
        target=_drain_to_log,
        args=(wda.stdout, LOG_PATH),
        daemon=True,
    )
    log_thread.start()

    def _cleanup(signum=None, frame=None) -> None:
        # Reset handlers first so a second signal can't re-enter
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        print("\nShutting down...")
        wda.terminate()
        iproxy.terminate()
        # Give the log thread up to 2s to flush its buffered write before exit
        log_thread.join(timeout=2)
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    if not wait_for_wda():
        print(f"ERROR: WDA did not come up. Check {LOG_PATH}", file=sys.stderr)
        _cleanup()

    print(f"WDA is live at {WDA_URL}")
    print("Run: python3 src/tap.py")

    wda.wait()
    _cleanup()


if __name__ == "__main__":
    main()
