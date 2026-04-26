"""
Unit tests for screenshot.py.

All network I/O is mocked at the boundary so no real device or WDA instance
is required.
"""

import base64
import sys
import pytest
import requests
from unittest.mock import patch, MagicMock, call

import screenshot


# ── take_screenshot ───────────────────────────────────────────────────────────

class TestTakeScreenshot:

    def test_returns_decoded_png_bytes(self):
        raw = b"\x89PNG\r\nfake_payload"
        b64_str = base64.b64encode(raw).decode()
        shot_resp = MagicMock()
        shot_resp.json.return_value = {"value": b64_str}

        with patch("screenshot.get_or_create_session", return_value="sess1"), \
             patch("requests.get", return_value=shot_resp) as mock_get:
            result = screenshot.take_screenshot()

        assert result == raw
        mock_get.assert_called_once_with(
            "http://127.0.0.1:8100/session/sess1/screenshot", timeout=10
        )

    def test_raises_key_error_on_malformed_wda_response(self):
        resp = MagicMock()
        resp.json.return_value = {}  # missing "value" key
        with patch("screenshot.get_or_create_session", return_value="sess1"), \
             patch("requests.get", return_value=resp):
            with pytest.raises(KeyError):
                screenshot.take_screenshot()

    def test_propagates_session_error(self):
        with patch("screenshot.get_or_create_session",
                   side_effect=RuntimeError("no session")):
            with pytest.raises(RuntimeError, match="no session"):
                screenshot.take_screenshot()

    def test_propagates_connection_error(self):
        with patch("screenshot.get_or_create_session", return_value="sess1"), \
             patch("requests.get", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                screenshot.take_screenshot()


# ── capture_loop ──────────────────────────────────────────────────────────────

class TestCaptureLoop:

    def test_calls_callback_with_screenshot_bytes(self):
        raw = b"\x89PNG\r\ndata"
        received = []

        def cb(img_bytes):
            received.append(img_bytes)
            raise StopIteration  # stop after first call

        with patch("screenshot.take_screenshot", return_value=raw), \
             patch("time.sleep"):
            try:
                screenshot.capture_loop(200, cb)
            except StopIteration:
                pass

        assert received == [raw]

    def test_sleep_uses_converted_interval(self):
        sleep_calls = []
        cb_calls = {"n": 0}

        def cb(img_bytes):
            cb_calls["n"] += 1
            if cb_calls["n"] >= 2:
                raise StopIteration

        with patch("screenshot.take_screenshot", return_value=b"img"), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            try:
                screenshot.capture_loop(500, cb)
            except StopIteration:
                pass

        assert sleep_calls == [0.5]

    def test_calls_callback_multiple_times(self):
        counts = {"n": 0}

        def cb(img_bytes):
            counts["n"] += 1
            if counts["n"] >= 3:
                raise StopIteration

        with patch("screenshot.take_screenshot", return_value=b"img"), \
             patch("time.sleep"):
            try:
                screenshot.capture_loop(100, cb)
            except StopIteration:
                pass

        assert counts["n"] == 3

    def test_propagates_take_screenshot_error(self):
        with patch("screenshot.take_screenshot",
                   side_effect=RuntimeError("WDA down")), \
             patch("time.sleep"):
            with pytest.raises(RuntimeError, match="WDA down"):
                screenshot.capture_loop(100, lambda b: None)


# ── main (CLI) ────────────────────────────────────────────────────────────────

class TestMain:

    def _status_ok(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {"ready": True}}
        return resp

    def test_exits_when_wda_unreachable(self):
        with patch("requests.get", side_effect=ConnectionRefusedError), \
             patch("sys.argv", ["screenshot.py"]):
            with pytest.raises(SystemExit) as exc_info:
                screenshot.main()
        assert exc_info.value.code != 0

    def test_exits_when_wda_not_ready(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {"ready": False}}
        with patch("requests.get", return_value=resp), \
             patch("sys.argv", ["screenshot.py"]):
            with pytest.raises(SystemExit) as exc_info:
                screenshot.main()
        assert exc_info.value.code != 0

    def test_prints_size_when_no_save_arg(self, capsys):
        raw = b"x" * 2048
        with patch("requests.get", return_value=self._status_ok()), \
             patch("screenshot.take_screenshot", return_value=raw), \
             patch("sys.argv", ["screenshot.py"]):
            screenshot.main()
        assert "KB" in capsys.readouterr().out

    def test_saves_file_when_save_arg_given(self, tmp_path):
        raw = b"\x89PNG\r\ndata"
        out_file = tmp_path / "screen.png"
        with patch("requests.get", return_value=self._status_ok()), \
             patch("screenshot.take_screenshot", return_value=raw), \
             patch("sys.argv", ["screenshot.py", "--save", str(out_file)]):
            screenshot.main()
        assert out_file.read_bytes() == raw

    def test_prints_saved_path(self, tmp_path, capsys):
        raw = b"\x89PNG\r\ndata"
        out_file = tmp_path / "screen.png"
        with patch("requests.get", return_value=self._status_ok()), \
             patch("screenshot.take_screenshot", return_value=raw), \
             patch("sys.argv", ["screenshot.py", "--save", str(out_file)]):
            screenshot.main()
        assert str(out_file) in capsys.readouterr().out
