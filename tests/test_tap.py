"""
Unit tests for tap.py.

All network and stdin I/O is mocked at the boundary so no real device or WDA
instance is required.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock

import tap


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

    def test_succeeds_on_first_attempt(self):
        with patch("tap.tap") as mock_tap:
            tap.tap_with_retry(10, 20)
        mock_tap.assert_called_once_with("initial_session", 10, 20)

    def test_reclaims_session_and_retries_after_tap_failure(self):
        call_n = {"n": 0}

        def tap_effect(session_id, x, y):
            call_n["n"] += 1
            if call_n["n"] == 1:
                raise RuntimeError("session stolen")
            # second call succeeds (returns None)

        with patch("tap.tap", side_effect=tap_effect), \
             patch("tap.get_or_create_session", return_value="new_session") as mock_get, \
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

        with patch("tap.tap", side_effect=tap_effect), \
             patch("tap.get_or_create_session", side_effect=sess_effect), \
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

    def test_prints_stderr_warning_after_10_consecutive_failures(self, capsys):
        call_n = {"n": 0}

        def tap_effect(session_id, x, y):
            call_n["n"] += 1
            if call_n["n"] <= 10:
                raise RuntimeError("always fails")

        with patch("tap.tap", side_effect=tap_effect), \
             patch("tap.get_or_create_session", return_value="s"), \
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

    def test_falls_back_to_x_y_when_coords_empty(self):
        assert tap.parse_coords(self._args(coords=[], x=50, y=75)) == [(50, 75)]

    def test_raises_on_missing_y(self):
        with pytest.raises(ValueError, match="Invalid coord"):
            tap.parse_coords(self._args(["700"]))

    def test_raises_on_extra_value(self):
        with pytest.raises(ValueError, match="Invalid coord"):
            tap.parse_coords(self._args(["700,400,999"]))


# ── per-cycle interval ────────────────────────────────────────────────────────

class TestPerCycleInterval:
    """Sleep must fire once per cycle, not once per tap."""

    def setup_method(self):
        tap._session_id = "sess"

    def test_sleep_called_once_per_cycle_not_per_tap(self):
        sleep_calls = []

        with patch("tap.tap_with_retry"), \
             patch("tap.check_keypress", return_value=""), \
             patch("tap.get_or_create_session", return_value="sess"), \
             patch("requests.get") as mock_get, \
             patch("requests.post"), \
             patch("termios.tcgetattr", return_value=[]), \
             patch("termios.tcsetattr"), \
             patch("tty.setcbreak"), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
             patch("sys.argv", ["tap.py", "--coords", "700,400", "335,250", "--count", "2"]):
            mock_get.return_value.json.return_value = {"value": {"ready": True}}
            tap.main()

        # 2 cycles, 2 coords each → 4 taps, but sleep only called once
        # (after cycle 1; cycle 2 is last so no trailing sleep)
        assert sleep_calls.count(1.0) == 1

    def test_sleep_uses_interval_arg(self):
        sleep_calls = []

        with patch("tap.tap_with_retry"), \
             patch("tap.check_keypress", return_value=""), \
             patch("tap.get_or_create_session", return_value="sess"), \
             patch("requests.get") as mock_get, \
             patch("requests.post"), \
             patch("termios.tcgetattr", return_value=[]), \
             patch("termios.tcsetattr"), \
             patch("tty.setcbreak"), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
             patch("sys.argv", ["tap.py", "--coords", "700,400", "--count", "3", "--interval", "0.25"]):
            mock_get.return_value.json.return_value = {"value": {"ready": True}}
            tap.main()

        assert sleep_calls == [0.25, 0.25]


# ── check_keypress ────────────────────────────────────────────────────────────

class TestCheckKeypress:

    def test_returns_key_when_stdin_has_data(self):
        with patch("select.select", return_value=([sys.stdin], [], [])), \
             patch.object(sys.stdin, "read", return_value="p"):
            assert tap.check_keypress() == "p"

    def test_returns_space_when_space_pressed(self):
        with patch("select.select", return_value=([sys.stdin], [], [])), \
             patch.object(sys.stdin, "read", return_value=" "):
            assert tap.check_keypress() == " "

    def test_returns_empty_string_when_no_input_pending(self):
        with patch("select.select", return_value=([], [], [])):
            assert tap.check_keypress() == ""
