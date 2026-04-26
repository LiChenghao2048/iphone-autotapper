"""
Unit tests for gestures/tap.py.

All network and stdin I/O is mocked at the boundary so no real device or WDA
instance is required.
"""

import sys
import time
import pytest
from unittest.mock import patch, MagicMock

from gestures import tap


# ── get_or_create_session ─────────────────────────────────────────────────────

class TestGetOrCreateSession:

    def test_returns_top_level_session_id(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": "abc123"}
        with patch("requests.post", return_value=resp) as mock_post:
            sid = tap.get_or_create_session()
        assert sid == "abc123"
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8100/session",
            json={"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}},
            timeout=10,
        )

    def test_falls_back_to_value_dict_session_id(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {"sessionId": "xyz789"}}
        with patch("requests.post", return_value=resp):
            assert tap.get_or_create_session() == "xyz789"

    def test_top_level_none_falls_back_to_value_dict(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": None, "value": {"sessionId": "fallback"}}
        with patch("requests.post", return_value=resp):
            assert tap.get_or_create_session() == "fallback"

    def test_raises_when_session_id_absent(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {}}
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="Could not create WDA session"):
                tap.get_or_create_session()


# ── tap ───────────────────────────────────────────────────────────────────────

class TestTap:

    def test_posts_correct_w3c_payload_on_success(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            tap.tap("sess1", 100, 200)
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8100/session/sess1/actions",
            json={
                "actions": [{
                    "type": "pointer",
                    "id": "finger",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": 100, "y": 200},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 50},
                        {"type": "pointerUp", "button": 0},
                    ],
                }]
            },
            timeout=5,
        )

    def test_raises_on_non_200_status(self):
        resp = MagicMock()
        resp.status_code = 404
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="WDA rejected tap"):
                tap.tap("sess1", 100, 200)

    def test_raises_on_500_status(self):
        resp = MagicMock()
        resp.status_code = 500
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="WDA rejected tap"):
                tap.tap("sess1", 0, 0)


# ── tap_with_retry ────────────────────────────────────────────────────────────

class TestTapWithRetry:

    def setup_method(self):
        tap._session_id = "initial_session"
        tap._pause_event.clear()

    def test_succeeds_on_first_attempt(self):
        with patch("gestures.tap.tap") as mock_tap:
            tap.tap_with_retry(10, 20)
        mock_tap.assert_called_once_with("initial_session", 10, 20)

    def test_reclaims_session_and_retries_after_tap_failure(self):
        call_n = {"n": 0}

        def tap_effect(session_id, x, y):
            call_n["n"] += 1
            if call_n["n"] == 1:
                raise RuntimeError("session stolen")
            # second call succeeds (returns None)

        with patch("gestures.tap.tap", side_effect=tap_effect), \
             patch("gestures.tap.get_or_create_session", return_value="new_session") as mock_get, \
             patch("time.sleep"):
            tap.tap_with_retry(10, 20)

        mock_get.assert_called_once()
        assert tap._session_id == "new_session"
        assert call_n["n"] == 2

    def test_retries_when_session_reclaim_itself_fails(self):
        # setup_method sets _session_id = "initial_session"; use it as the stale value
        used_sessions = []
        tap_n = {"n": 0}

        def tap_effect(session_id, x, y):
            used_sessions.append(session_id)
            tap_n["n"] += 1
            if tap_n["n"] < 3:
                raise RuntimeError("tap fail")

        sess_n = {"n": 0}

        def sess_effect():
            sess_n["n"] += 1
            if sess_n["n"] == 1:
                raise RuntimeError("WDA down")
            return "recovered"

        with patch("gestures.tap.tap", side_effect=tap_effect), \
             patch("gestures.tap.get_or_create_session", side_effect=sess_effect), \
             patch("time.sleep"):
            tap.tap_with_retry(5, 10)

        assert tap_n["n"] == 3
        assert sess_n["n"] == 2
        assert tap._session_id == "recovered"
        # First two taps use the stale session (first reclaim failed)
        assert used_sessions[0] == "initial_session"
        assert used_sessions[1] == "initial_session"
        # Third tap uses the successfully recovered session
        assert used_sessions[2] == "recovered"

    def test_exits_immediately_when_paused_before_tap(self):
        tap._pause_event.set()
        with patch("gestures.tap.tap") as mock_tap:
            tap.tap_with_retry(0, 0)
        mock_tap.assert_not_called()

    def test_exits_retry_loop_when_paused_between_retries(self):
        tap_calls = {"n": 0}

        def tap_effect(session_id, x, y):
            tap_calls["n"] += 1
            raise RuntimeError("session stolen")

        def reclaim_effect():
            tap._pause_event.set()   # pause fires during session reclaim
            return "new_session"

        with patch("gestures.tap.tap", side_effect=tap_effect), \
             patch("gestures.tap.get_or_create_session", side_effect=reclaim_effect), \
             patch("time.sleep"):
            tap.tap_with_retry(0, 0)

        # only the first tap attempt was made; loop exited after pause set in reclaim
        assert tap_calls["n"] == 1

    def test_prints_stderr_warning_after_10_consecutive_failures(self, capsys):
        call_n = {"n": 0}

        def tap_effect(session_id, x, y):
            call_n["n"] += 1
            if call_n["n"] <= 10:
                raise RuntimeError("always fails")

        with patch("gestures.tap.tap", side_effect=tap_effect), \
             patch("gestures.tap.get_or_create_session", return_value="s"), \
             patch("time.sleep"):
            tap.tap_with_retry(0, 0)

        assert "still retrying" in capsys.readouterr().err


# ── parse_coords ─────────────────────────────────────────────────────────────

class TestParseCoords:

    def _args(self, coords=None, x=215, y=466):
        """Build a minimal args namespace."""
        ns = MagicMock()
        ns.coords = coords
        ns.x = x
        ns.y = y
        return ns

    def test_single_coord_string(self):
        assert tap.parse_coords(self._args(["700,400"])) == [(700, 400)]

    def test_multiple_coord_strings(self):
        assert tap.parse_coords(self._args(["700,400", "335,250", "860,400"])) == [
            (700, 400), (335, 250), (860, 400)
        ]

    def test_falls_back_to_x_y_when_coords_none(self):
        assert tap.parse_coords(self._args(coords=None, x=100, y=200)) == [(100, 200)]

    def test_coords_takes_precedence_over_x_y(self):
        # --coords should win even when --x/--y are also set
        assert tap.parse_coords(self._args(coords=["700,400"], x=999, y=999)) == [(700, 400)]

    def test_raises_on_missing_y(self):
        with pytest.raises(ValueError, match="Invalid coord"):
            tap.parse_coords(self._args(["700"]))

    def test_raises_on_extra_value(self):
        with pytest.raises(ValueError, match="Invalid coord"):
            tap.parse_coords(self._args(["700,400,999"]))

    def test_raises_on_non_integer_value(self):
        with pytest.raises(ValueError, match="Invalid coord"):
            tap.parse_coords(self._args(["700.5,400"]))

    def test_main_exits_with_error_on_bad_coords(self):
        with patch("sys.argv", ["tap.py", "--coords", "abc,def"]):
            with pytest.raises(SystemExit) as exc_info:
                tap.main()
        assert exc_info.value.code != 0


# ── _sleep_interval ──────────────────────────────────────────────────────────

class TestSleepInterval:

    def setup_method(self):
        tap._pause_event.clear()

    def test_returns_early_when_paused(self):
        tap._pause_event.set()
        start = time.monotonic()
        tap._sleep_interval(10.0)
        assert time.monotonic() - start < 1.0

    def test_sleeps_approximately_full_duration_when_running(self):
        start = time.monotonic()
        tap._sleep_interval(0.1)
        elapsed = time.monotonic() - start
        assert 0.08 <= elapsed <= 0.5


# ── per-cycle interval ────────────────────────────────────────────────────────

class TestPerCycleInterval:
    """_sleep_interval must fire once per cycle, not once per tap."""

    def setup_method(self):
        tap._session_id = "sess"
        tap._pause_event.clear()

    def test_sleep_called_once_per_cycle_not_per_tap(self):
        interval_calls = []

        with patch("requests.get") as mock_get, \
             patch("gestures.tap.tap_with_retry"), \
             patch("gestures.tap.get_or_create_session", return_value="sess"), \
             patch("termios.tcgetattr", return_value=[]), \
             patch("termios.tcsetattr"), \
             patch("tty.setcbreak"), \
             patch("threading.Thread"), \
             patch("gestures.tap._sleep_interval", side_effect=lambda s: interval_calls.append(s)), \
             patch("sys.argv", ["tap.py", "--coords", "700,400", "335,250", "--count", "2"]):
            mock_get.return_value.json.return_value = {"value": {"ready": True}}
            tap.main()

        # 2 cycles, 2 coords each — interval fires only after cycle 1
        assert interval_calls == [1.0]

    def test_sleep_uses_interval_arg(self):
        interval_calls = []

        with patch("requests.get") as mock_get, \
             patch("gestures.tap.tap_with_retry"), \
             patch("gestures.tap.get_or_create_session", return_value="sess"), \
             patch("termios.tcgetattr", return_value=[]), \
             patch("termios.tcsetattr"), \
             patch("tty.setcbreak"), \
             patch("threading.Thread"), \
             patch("gestures.tap._sleep_interval", side_effect=lambda s: interval_calls.append(s)), \
             patch("sys.argv", ["tap.py", "--coords", "700,400", "--count", "3", "--interval", "0.25"]):
            mock_get.return_value.json.return_value = {"value": {"ready": True}}
            tap.main()

        assert interval_calls == [0.25, 0.25]


# ── _keyboard_listener ────────────────────────────────────────────────────────

class TestKeyboardListener:

    def setup_method(self):
        tap._pause_event.clear()

    def _run_listener_with_keys(self, keys):
        """Feed key sequence into _keyboard_listener; stop via StopIteration."""
        with patch.object(sys.stdin, "read", side_effect=keys + [StopIteration]):
            try:
                tap._keyboard_listener()
            except StopIteration:
                pass

    def test_space_pauses(self):
        self._run_listener_with_keys([" "])
        assert tap._pause_event.is_set() is True

    def test_p_pauses(self):
        self._run_listener_with_keys(["p"])
        assert tap._pause_event.is_set() is True

    def test_space_toggles_resume(self):
        self._run_listener_with_keys([" ", " "])
        assert tap._pause_event.is_set() is False

    def test_other_keys_ignored(self):
        self._run_listener_with_keys(["x", "q", "\n"])
        assert tap._pause_event.is_set() is False

    def test_pause_then_resume_then_pause(self):
        self._run_listener_with_keys([" ", " ", " "])
        assert tap._pause_event.is_set() is True
