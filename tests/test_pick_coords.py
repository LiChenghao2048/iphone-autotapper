"""
Unit and integration tests for pick_coords.py.

Module-level functions are tested with mocked HTTP calls.
Handler routes are tested via a real (loopback) HTTP server built with
make_handler() — no WDA device required.
"""

import argparse
import base64
import http.server
import io
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image
from unittest.mock import patch, MagicMock

import pick_coords


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_png(width: int = 9, height: int = 18) -> bytes:
    img = Image.new("RGB", (width, height), color=(0, 200, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _http_get(port: int, path: str):
    """Return (status, content_type, body_bytes) for a GET to the test server."""
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=3) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


# ── screenshot_to_b64 ─────────────────────────────────────────────────────────

class TestScreenshotToB64:

    def test_returns_correct_pixel_dimensions(self):
        _, w, h = pick_coords.screenshot_to_b64(_make_png(12, 24))
        assert w == 12
        assert h == 24

    def test_returned_b64_decodes_to_valid_png_with_same_dimensions(self):
        b64, _, _ = pick_coords.screenshot_to_b64(_make_png(12, 24))
        decoded_img = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert decoded_img.size == (12, 24)

    def test_returns_str_not_bytes(self):
        b64, _, _ = pick_coords.screenshot_to_b64(_make_png())
        assert isinstance(b64, str)


# ── build_html ────────────────────────────────────────────────────────────────

class TestBuildHtml:

    def test_embeds_b64_image_data(self):
        state = {"b64": "FAKEB64DATA", "px_w": 9, "px_h": 18}
        html = pick_coords.build_html(state)
        assert "FAKEB64DATA" in html

    def test_embeds_pixel_dimensions(self):
        state = {"b64": "x", "px_w": 300, "px_h": 600}
        html = pick_coords.build_html(state)
        assert "300" in html
        assert "600" in html

    def test_embeds_scale_constant(self):
        state = {"b64": "x", "px_w": 1, "px_h": 1}
        html = pick_coords.build_html(state)
        assert str(pick_coords.SCALE) in html

    def test_different_states_produce_different_html(self):
        state_a = {"b64": "AAA", "px_w": 9, "px_h": 18}
        state_b = {"b64": "BBB", "px_w": 9, "px_h": 18}
        assert pick_coords.build_html(state_a) != pick_coords.build_html(state_b)

    def test_bounds_check_uses_exclusive_upper_bound(self):
        # Locks the >= operator so an off-by-one regression is caught at the
        # template level without requiring a browser.
        state = {"b64": "x", "px_w": 9, "px_h": 18}
        html = pick_coords.build_html(state)
        assert "tx >= maxTx" in html
        assert "ty >= maxTy" in html

    def test_out_of_range_error_message_shows_max_minus_one(self):
        # The valid upper bound is maxTx-1, so the error message must display
        # (maxTx - 1) and (maxTy - 1), not maxTx/maxTy.
        state = {"b64": "x", "px_w": 9, "px_h": 18}
        html = pick_coords.build_html(state)
        assert "(maxTx - 1)" in html
        assert "(maxTy - 1)" in html

    def test_non_integer_input_error_message(self):
        state = {"b64": "x", "px_w": 9, "px_h": 18}
        html = pick_coords.build_html(state)
        assert "Enter integers in both fields" in html


# ── Handler (via make_handler + real loopback server) ─────────────────────────

@pytest.fixture
def handler_server(tmp_path):
    """
    Spin up a loopback HTTP server using make_handler() on a free port.
    Yields (port, img_path, state) and shuts down after the test.
    """
    png = _make_png(9, 18)
    img_path = tmp_path / "test.png"
    img_path.write_bytes(png)

    b64, px_w, px_h = pick_coords.screenshot_to_b64(png)
    state = {"b64": b64, "px_w": px_w, "px_h": px_h}
    args = argparse.Namespace(img=str(img_path))

    handler_cls = pick_coords.make_handler(state, args)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    yield port, img_path, state

    server.shutdown()
    t.join(timeout=2)


class TestHandler:

    # ── root route ────────────────────────────────────────────────────────────

    def test_root_serves_200_html(self, handler_server):
        port, _, _ = handler_server
        status, ctype, _ = _http_get(port, "/")
        assert status == 200
        assert "text/html" in ctype

    def test_root_html_contains_embedded_image(self, handler_server):
        port, _, _ = handler_server
        _, _, body = _http_get(port, "/")
        assert b"data:image/png;base64," in body

    def test_root_html_contains_picker_title(self, handler_server):
        port, _, _ = handler_server
        _, _, body = _http_get(port, "/")
        assert b"iPhone Coordinate Picker" in body

    def test_unknown_path_falls_through_to_html(self, handler_server):
        port, _, _ = handler_server
        # Any path that is not /click* or /refresh serves the picker HTML
        status, ctype, body = _http_get(port, "/favicon.ico")
        assert status == 200
        assert "text/html" in ctype
        assert b"iPhone Coordinate Picker" in body

    # ── /click route ──────────────────────────────────────────────────────────

    def test_click_returns_200(self, handler_server):
        port, _, _ = handler_server
        status, _, _ = _http_get(port, "/click?tx=100&ty=200&px=300&py=600")
        assert status == 200

    def test_click_with_missing_params_uses_question_mark_defaults(self, handler_server, capsys):
        port, _, _ = handler_server
        status, _, _ = _http_get(port, "/click")
        assert status == 200
        # The handler prints the tap.py command before sending the response, so
        # the print completes before _http_get returns — capsys captures it reliably.
        assert "?" in capsys.readouterr().out

    # ── /refresh route ────────────────────────────────────────────────────────

    def test_refresh_returns_200_json(self, handler_server):
        port, _, _ = handler_server
        status, ctype, body = _http_get(port, "/refresh")
        assert status == 200
        assert "application/json" in ctype
        data = json.loads(body)
        assert {"b64", "px_w", "px_h"} <= data.keys()

    def test_refresh_returns_correct_dimensions(self, handler_server):
        port, _, _ = handler_server
        _, _, body = _http_get(port, "/refresh")
        data = json.loads(body)
        assert data["px_w"] == 9
        assert data["px_h"] == 18

    def test_refresh_updates_shared_state(self, handler_server):
        port, img_path, state = handler_server
        original_b64 = state["b64"]

        # Overwrite the backing file with a PNG of different dimensions
        new_png = _make_png(30, 60)
        img_path.write_bytes(new_png)

        _http_get(port, "/refresh")

        # State dict must now reflect the new image
        assert state["b64"] != original_b64
        assert state["px_w"] == 30
        assert state["px_h"] == 60

    def test_refresh_returns_500_when_img_file_missing(self, tmp_path):
        """Handler responds 500 when the backing image file has been deleted."""
        png = _make_png(6, 6)
        img_path = tmp_path / "gone.png"
        img_path.write_bytes(png)

        b64, px_w, px_h = pick_coords.screenshot_to_b64(png)
        state = {"b64": b64, "px_w": px_w, "px_h": px_h}
        args = argparse.Namespace(img=str(img_path))

        handler_cls = pick_coords.make_handler(state, args)
        server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            img_path.unlink()  # delete the file so refresh fails
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/refresh", timeout=3)
            assert exc.value.code == 500
        finally:
            server.shutdown()
            t.join(timeout=2)

    def test_refresh_via_take_screenshot_when_no_img(self):
        """In screenshot mode (args.img=None), refresh calls take_screenshot()."""
        png = _make_png(6, 12)
        b64, px_w, px_h = pick_coords.screenshot_to_b64(png)
        state = {"b64": b64, "px_w": px_w, "px_h": px_h}
        args = argparse.Namespace(img=None)

        handler_cls = pick_coords.make_handler(state, args)
        server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            with patch("pick_coords.take_screenshot", return_value=png) as mock_ss:
                status, ctype, body = _http_get(port, "/refresh")
            assert status == 200
            mock_ss.assert_called_once()
            data = json.loads(body)
            assert data["px_w"] == 6
            assert data["px_h"] == 12
        finally:
            server.shutdown()
            t.join(timeout=2)


# ── /tap route ────────────────────────────────────────────────────────────────

class TestTapEndpoint:

    def test_tap_returns_ok_true_and_calls_tap(self, handler_server):
        port, _, _ = handler_server
        with patch("pick_coords.get_or_create_session", return_value="fake-sid"), \
             patch("pick_coords.tap") as mock_tap:
            status, ctype, body = _http_get(port, "/tap?tx=100&ty=200")
        assert status == 200
        assert "application/json" in ctype
        data = json.loads(body)
        assert data == {"ok": True}
        mock_tap.assert_called_once_with("fake-sid", 100, 200)

    def test_tap_returns_ok_false_on_session_error(self, handler_server):
        port, _, _ = handler_server
        with patch("pick_coords.get_or_create_session", side_effect=RuntimeError("WDA down")):
            status, ctype, body = _http_get(port, "/tap?tx=50&ty=60")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
        assert "WDA down" in data["error"]

    def test_tap_returns_ok_false_on_tap_error(self, handler_server):
        port, _, _ = handler_server
        with patch("pick_coords.get_or_create_session", return_value="sid"), \
             patch("pick_coords.tap", side_effect=RuntimeError("session stolen")):
            status, ctype, body = _http_get(port, "/tap?tx=10&ty=20")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
        assert "session stolen" in data["error"]

    def test_tap_missing_params_returns_ok_false(self, handler_server):
        port, _, _ = handler_server
        status, ctype, body = _http_get(port, "/tap")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
        assert "error" in data

    def test_tap_non_integer_params_returns_ok_false(self, handler_server):
        port, _, _ = handler_server
        status, ctype, body = _http_get(port, "/tap?tx=foo&ty=bar")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
        assert "error" in data

    def test_tap_partial_params_returns_ok_false(self, handler_server):
        port, _, _ = handler_server
        status, ctype, body = _http_get(port, "/tap?tx=100")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
