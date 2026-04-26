"""
Unit tests for gestures/swipe.py.

All network I/O is mocked at the boundary so no real device or WDA instance
is required.
"""

import pytest
import requests
from unittest.mock import patch, MagicMock

from gestures import swipe


# ── get_or_create_session ─────────────────────────────────────────────────────

class TestGetOrCreateSession:

    def test_returns_top_level_session_id(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": "abc123"}
        with patch("requests.post", return_value=resp) as mock_post:
            sid = swipe.get_or_create_session()
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
            assert swipe.get_or_create_session() == "xyz789"

    def test_top_level_none_falls_back_to_value_dict(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": None, "value": {"sessionId": "fallback"}}
        with patch("requests.post", return_value=resp):
            assert swipe.get_or_create_session() == "fallback"

    def test_raises_when_session_id_absent(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {}}
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="Could not create WDA session"):
                swipe.get_or_create_session()


# ── swipe ─────────────────────────────────────────────────────────────────────

class TestSwipe:

    def test_posts_correct_w3c_payload_on_success(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 100, 200, 300, 400, duration_ms=500)
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8100/session/sess1/actions",
            json={
                "actions": [{
                    "type": "pointer",
                    "id": "finger",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0,   "x": 100, "y": 200},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": 500, "x": 300, "y": 400},
                        {"type": "pointerUp",   "button": 0},
                    ],
                }]
            },
            timeout=5,  # max(5, 0.5+2) = 5
        )

    def test_raises_on_non_200_status(self):
        resp = MagicMock()
        resp.status_code = 404
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="WDA rejected swipe"):
                swipe.swipe("sess1", 0, 0, 100, 100)

    def test_raises_on_500_status(self):
        resp = MagicMock()
        resp.status_code = 500
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="WDA rejected swipe"):
                swipe.swipe("sess1", 0, 0, 100, 100)

    def test_raises_value_error_on_zero_duration(self):
        with pytest.raises(ValueError, match="duration_ms must be positive"):
            swipe.swipe("sess1", 0, 0, 100, 100, duration_ms=0)

    def test_raises_value_error_on_negative_duration(self):
        with pytest.raises(ValueError, match="duration_ms must be positive"):
            swipe.swipe("sess1", 0, 0, 100, 100, duration_ms=-100)

    def test_raises_on_connection_error(self):
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                swipe.swipe("sess1", 0, 0, 100, 100)

    def test_raises_on_timeout(self):
        with patch("requests.post", side_effect=requests.Timeout("timed out")):
            with pytest.raises(requests.Timeout):
                swipe.swipe("sess1", 0, 0, 100, 100)

    def test_default_duration_is_500ms(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 0, 0, 100, 100)
        payload = mock_post.call_args.kwargs["json"]
        move_action = payload["actions"][0]["actions"][2]
        assert move_action["duration"] == 500

    def test_timeout_is_at_least_5_seconds_for_short_swipe(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 0, 0, 100, 100, duration_ms=100)
        # max(5, 0.1+2) = 5
        assert mock_post.call_args.kwargs["timeout"] == 5

    def test_timeout_exceeds_duration_for_long_swipe(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 0, 0, 100, 100, duration_ms=4000)
        # max(5, 4+2) = 6
        assert mock_post.call_args.kwargs["timeout"] == 6

    def test_duration_propagated_to_pointer_move(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 10, 20, 30, 40, duration_ms=750)
        actions = mock_post.call_args.kwargs["json"]["actions"][0]["actions"]
        assert actions[2]["duration"] == 750

    def test_start_coordinates_in_initial_pointer_move(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 111, 222, 333, 444)
        actions = mock_post.call_args.kwargs["json"]["actions"][0]["actions"]
        assert actions[0]["x"] == 111
        assert actions[0]["y"] == 222

    def test_end_coordinates_in_second_pointer_move(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 111, 222, 333, 444)
        actions = mock_post.call_args.kwargs["json"]["actions"][0]["actions"]
        assert actions[2]["x"] == 333
        assert actions[2]["y"] == 444

    def test_pointer_type_is_touch(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.post", return_value=resp) as mock_post:
            swipe.swipe("sess1", 0, 0, 1, 1)
        params = mock_post.call_args.kwargs["json"]["actions"][0]["parameters"]
        assert params["pointerType"] == "touch"


# ── main ──────────────────────────────────────────────────────────────────────

class TestMain:

    def test_exits_when_wda_unreachable(self):
        with patch("requests.get", side_effect=ConnectionRefusedError), \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0

    def test_exits_when_wda_not_ready(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {"ready": False}}
        with patch("requests.get", return_value=resp), \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0

    def test_calls_swipe_with_parsed_args(self):
        status_resp = MagicMock()
        status_resp.json.return_value = {"value": {"ready": True}}
        with patch("requests.get", return_value=status_resp), \
             patch("gestures.swipe.get_or_create_session", return_value="sess1"), \
             patch("gestures.swipe.swipe") as mock_swipe, \
             patch("sys.argv", ["swipe.py", "--x1", "10", "--y1", "20", "--x2", "30", "--y2", "40", "--duration", "300"]):
            swipe.main()
        mock_swipe.assert_called_once_with("sess1", 10, 20, 30, 40, 300)

    def test_uses_default_duration_when_omitted(self):
        status_resp = MagicMock()
        status_resp.json.return_value = {"value": {"ready": True}}
        with patch("requests.get", return_value=status_resp), \
             patch("gestures.swipe.get_or_create_session", return_value="sess1"), \
             patch("gestures.swipe.swipe") as mock_swipe, \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100"]):
            swipe.main()
        mock_swipe.assert_called_once_with("sess1", 0, 0, 100, 100, 500)

    def test_missing_required_arg_exits_with_error(self):
        with patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0

    def test_exits_when_swipe_raises_runtime_error(self):
        status_resp = MagicMock()
        status_resp.json.return_value = {"value": {"ready": True}}
        with patch("requests.get", return_value=status_resp), \
             patch("gestures.swipe.get_or_create_session", return_value="sess1"), \
             patch("gestures.swipe.swipe", side_effect=RuntimeError("WDA rejected swipe (HTTP 500)")), \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0

    def test_exits_when_duration_is_zero(self):
        status_resp = MagicMock()
        status_resp.json.return_value = {"value": {"ready": True}}
        with patch("requests.get", return_value=status_resp), \
             patch("gestures.swipe.get_or_create_session", return_value="sess1"), \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100", "--duration", "0"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0

    def test_exits_when_swipe_raises_connection_error(self):
        status_resp = MagicMock()
        status_resp.json.return_value = {"value": {"ready": True}}
        with patch("requests.get", return_value=status_resp), \
             patch("gestures.swipe.get_or_create_session", return_value="sess1"), \
             patch("gestures.swipe.swipe", side_effect=requests.ConnectionError("refused")), \
             patch("sys.argv", ["swipe.py", "--x1", "0", "--y1", "0", "--x2", "100", "--y2", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                swipe.main()
        assert exc_info.value.code != 0
