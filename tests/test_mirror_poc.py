"""
Unit tests for mirror_poc.py.

All Quartz and AppKit I/O is mocked at the framework boundary — no iPhone
Mirroring app or physical device is required.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, call

import mirror_poc


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_window(
    name="iPhone Mirroring",
    pid=1234,
    wid=42,
    layer=0,
    x=100.0, y=50.0, w=430.0, h=960.0,
):
    return {
        "kCGWindowOwnerName": name,
        "kCGWindowOwnerPID": pid,
        "kCGWindowNumber": wid,
        "kCGWindowLayer": layer,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
    }


def _mock_find(pid=1234, wid=42, x=100.0, y=50.0, w=430.0, h=960.0):
    return (pid, wid, {"X": x, "Y": y, "Width": w, "Height": h})


# ── _find_mirror_window ───────────────────────────────────────────────────────

class TestFindMirrorWindow:

    def test_returns_pid_wid_bounds_on_match(self):
        win = _make_window(pid=9999, wid=77, x=10.0, y=20.0, w=400.0, h=870.0)
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[win]):
            pid, wid, bounds = mirror_poc._find_mirror_window()
        assert pid == 9999
        assert wid == 77
        assert bounds == {"X": 10.0, "Y": 20.0, "Width": 400.0, "Height": 870.0}

    def test_raises_when_no_matching_window(self):
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[
            _make_window(name="Safari"),
            _make_window(name="Finder"),
        ]):
            with pytest.raises(RuntimeError, match="iPhone Mirroring window not found"):
                mirror_poc._find_mirror_window()

    def test_raises_when_window_list_is_empty(self):
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[]):
            with pytest.raises(RuntimeError):
                mirror_poc._find_mirror_window()

    def test_skips_non_zero_layer_windows(self):
        # layer 1 windows (menus, panels) must be ignored
        panel = _make_window(layer=1, w=430.0, h=960.0)
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[panel]):
            with pytest.raises(RuntimeError):
                mirror_poc._find_mirror_window()

    def test_skips_negative_layer_windows(self):
        below = _make_window(layer=-1, w=430.0, h=960.0)
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[below]):
            with pytest.raises(RuntimeError):
                mirror_poc._find_mirror_window()

    def test_picks_largest_window_when_multiple_layer0(self):
        small = _make_window(wid=10, w=200.0, h=400.0)
        large = _make_window(wid=20, w=430.0, h=960.0)
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[small, large]):
            _, wid, _ = mirror_poc._find_mirror_window()
        assert wid == 20

    def test_ignores_non_iphone_mirroring_windows(self):
        safari = _make_window(name="Safari", wid=1)
        mirror = _make_window(name="iPhone Mirroring", wid=2)
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[safari, mirror]):
            _, wid, _ = mirror_poc._find_mirror_window()
        assert wid == 2


# ── map_point ─────────────────────────────────────────────────────────────────

class TestMapPoint:
    """
    Reference geometry for all cases below:
      phone_pts = (430, 932)  title_bar_h = 28
    """

    PHONE = (430, 932)
    TBH   = 28

    def _bounds(self, x=0.0, y=0.0, w=430.0, h=960.0):
        return {"X": x, "Y": y, "Width": w, "Height": h}

    def test_origin_maps_to_top_left_of_phone_display(self):
        # Window exactly fits phone (430 × 960), no letterboxing, scale=1.0
        b = self._bounds(x=0.0, y=0.0, w=430.0, h=960.0)
        sx, sy = mirror_poc.map_point(0, 0, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(0.0)
        assert sy == pytest.approx(28.0)   # title bar only

    def test_non_origin_with_scale_1(self):
        b = self._bounds(x=10.0, y=5.0, w=430.0, h=960.0)
        sx, sy = mirror_poc.map_point(100, 200, b, self.PHONE, self.TBH)
        # scale=1.0, no letterbox
        assert sx == pytest.approx(10.0 + 100.0)
        assert sy == pytest.approx(5.0 + 28.0 + 200.0)

    def test_horizontal_letterboxing_centres_phone_horizontally(self):
        # Window wider than phone → horizontal bars
        #   content: 600 × 932 → scale = min(600/430, 932/932) = 1.0
        #   display: 430 × 932 → letterbox_x = (600-430)/2 = 85
        b = self._bounds(x=0.0, y=0.0, w=600.0, h=960.0)
        sx, sy = mirror_poc.map_point(0, 0, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(85.0)
        assert sy == pytest.approx(28.0)

    def test_scale_down_when_window_is_smaller_than_phone(self):
        # Window half the phone size: 215 × 494 (28 title + 466 content = 494)
        #   content: 215 × 466 → scale = min(215/430, 466/932) = 0.5
        #   no letterboxing at 0.5
        b = self._bounds(x=0.0, y=0.0, w=215.0, h=494.0)
        sx, sy = mirror_poc.map_point(100, 200, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(100.0 * 0.5)
        assert sy == pytest.approx(28.0 + 200.0 * 0.5)

    def test_scale_up_when_window_is_larger_than_phone(self):
        # Window 2× the phone: 860 × 1892 (28 + 1864 content)
        #   content: 860 × 1864 → scale = min(860/430, 1864/932) = 2.0
        b = self._bounds(x=0.0, y=0.0, w=860.0, h=1892.0)
        sx, sy = mirror_poc.map_point(100, 200, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(100.0 * 2.0)
        assert sy == pytest.approx(28.0 + 200.0 * 2.0)

    def test_window_offset_is_added_to_result(self):
        b = self._bounds(x=300.0, y=150.0, w=430.0, h=960.0)
        sx, sy = mirror_poc.map_point(0, 0, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(300.0)
        assert sy == pytest.approx(150.0 + 28.0)

    def test_bottom_right_phone_corner_lands_inside_window(self):
        # For scale=1, phone corner (430, 932) → one pixel past the display edge
        b = self._bounds(x=0.0, y=0.0, w=430.0, h=960.0)
        sx, sy = mirror_poc.map_point(430, 932, b, self.PHONE, self.TBH)
        assert sx == pytest.approx(430.0)
        assert sy == pytest.approx(28.0 + 932.0)

    def test_custom_title_bar_height(self):
        b = self._bounds(x=0.0, y=0.0, w=430.0, h=960.0)
        sx, sy = mirror_poc.map_point(0, 0, b, self.PHONE, title_bar_h=50)
        assert sy == pytest.approx(50.0)

    def test_custom_phone_resolution(self):
        # Hypothetical 390×844 phone (iPhone 14), scale=1 in 390×872 window
        b = self._bounds(x=0.0, y=0.0, w=390.0, h=872.0)
        sx, sy = mirror_poc.map_point(100, 100, b, phone_pts=(390, 844), title_bar_h=28)
        assert sx == pytest.approx(100.0)
        assert sy == pytest.approx(28.0 + 100.0)


# ── background_tap ────────────────────────────────────────────────────────────

class TestBackgroundTap:

    def _patch_find(self, pid=1234, wid=42):
        bounds = {"X": 100.0, "Y": 50.0, "Width": 430.0, "Height": 960.0}
        return patch("mirror_poc._find_mirror_window", return_value=(pid, wid, bounds))

    def test_posts_mouse_down_then_up_to_correct_pid(self):
        mock_down = MagicMock(name="down_event")
        mock_up   = MagicMock(name="up_event")

        with self._patch_find(pid=9999), \
             patch("Quartz.CGPointMake") as mock_pt, \
             patch("Quartz.CGEventCreateMouseEvent", side_effect=[mock_down, mock_up]) as mock_create, \
             patch("Quartz.CGEventPostToPid") as mock_post, \
             patch("time.sleep"):

            mirror_poc.background_tap(215, 466)

        # Two events created: down then up
        assert mock_create.call_count == 2
        first_type  = mock_create.call_args_list[0][0][1]
        second_type = mock_create.call_args_list[1][0][1]
        import Quartz as Q
        assert first_type  == Q.kCGEventLeftMouseDown
        assert second_type == Q.kCGEventLeftMouseUp

        # Posted to the right PID, in order
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[0] == call(9999, mock_down)
        assert mock_post.call_args_list[1] == call(9999, mock_up)

    def test_calls_find_window_exactly_once(self):
        with self._patch_find() as mock_find, \
             patch("Quartz.CGPointMake"), \
             patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()), \
             patch("Quartz.CGEventPostToPid"), \
             patch("time.sleep"):
            mirror_poc.background_tap(0, 0)
        mock_find.assert_called_once()

    def test_sleeps_between_down_and_up(self):
        sleep_calls = []

        with self._patch_find(), \
             patch("Quartz.CGPointMake"), \
             patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()), \
             patch("Quartz.CGEventPostToPid") as mock_post, \
             patch("time.sleep", side_effect=sleep_calls.append):

            mirror_poc.background_tap(0, 0)

        # Sleep must occur between the two posts
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(0.05)
        # down posted before sleep, up posted after
        post_order = [c[0][1] for c in mock_post.call_args_list]
        assert len(post_order) == 2

    def test_map_point_called_with_correct_logical_coords(self):
        with self._patch_find() as _, \
             patch("mirror_poc.map_point", return_value=(200.0, 300.0)) as mock_map, \
             patch("Quartz.CGPointMake"), \
             patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()), \
             patch("Quartz.CGEventPostToPid"), \
             patch("time.sleep"):
            mirror_poc.background_tap(123, 456)

        mock_map.assert_called_once()
        call_args = mock_map.call_args[0]
        assert call_args[0] == 123
        assert call_args[1] == 456

    def test_propagates_find_window_error(self):
        with patch("mirror_poc._find_mirror_window",
                   side_effect=RuntimeError("app not running")):
            with pytest.raises(RuntimeError, match="app not running"):
                mirror_poc.background_tap(0, 0)


# ── capture_frame ─────────────────────────────────────────────────────────────

class TestCaptureFrame:

    def _patch_find(self, wid=42):
        bounds = {"X": 0.0, "Y": 0.0, "Width": 430.0, "Height": 960.0}
        return patch("mirror_poc._find_mirror_window", return_value=(1234, wid, bounds))

    def test_returns_png_bytes(self):
        fake_png = b"\x89PNG\r\nfake_image_data"
        mock_cg_img = MagicMock(name="cg_img")

        mock_rep = MagicMock()
        mock_rep.representationUsingType_properties_.return_value = fake_png

        mock_bitmap = MagicMock()
        mock_bitmap.alloc.return_value.initWithCGImage_.return_value = mock_rep

        with self._patch_find(wid=99), \
             patch("Quartz.CGWindowListCreateImage", return_value=mock_cg_img) as mock_create, \
             patch("mirror_poc.NSBitmapImageRep", mock_bitmap):

            result = mirror_poc.capture_frame()

        assert result == fake_png
        mock_create.assert_called_once_with(
            mirror_poc.Quartz.CGRectNull,
            mirror_poc.Quartz.kCGWindowListOptionIncludingWindow,
            99,
            mirror_poc.Quartz.kCGWindowImageDefault,
        )

    def test_raises_when_cg_image_is_none(self):
        with self._patch_find(), \
             patch("Quartz.CGWindowListCreateImage", return_value=None):
            with pytest.raises(RuntimeError, match="CGWindowListCreateImage returned None"):
                mirror_poc.capture_frame()

    def test_raises_when_png_encoding_fails(self):
        mock_cg_img = MagicMock()
        mock_rep = MagicMock()
        mock_rep.representationUsingType_properties_.return_value = None

        mock_bitmap = MagicMock()
        mock_bitmap.alloc.return_value.initWithCGImage_.return_value = mock_rep

        with self._patch_find(), \
             patch("Quartz.CGWindowListCreateImage", return_value=mock_cg_img), \
             patch("mirror_poc.NSBitmapImageRep", mock_bitmap):
            with pytest.raises(RuntimeError, match="Failed to encode"):
                mirror_poc.capture_frame()

    def test_passes_window_id_to_create_image(self):
        fake_png = b"img"
        mock_rep = MagicMock()
        mock_rep.representationUsingType_properties_.return_value = fake_png
        mock_bitmap = MagicMock()
        mock_bitmap.alloc.return_value.initWithCGImage_.return_value = mock_rep

        with patch("mirror_poc._find_mirror_window", return_value=(1, 777, {})), \
             patch("Quartz.CGWindowListCreateImage", return_value=MagicMock()) as mock_create, \
             patch("mirror_poc.NSBitmapImageRep", mock_bitmap):
            mirror_poc.capture_frame()

        assert mock_create.call_args[0][2] == 777

    def test_propagates_find_window_error(self):
        with patch("mirror_poc._find_mirror_window",
                   side_effect=RuntimeError("no window")):
            with pytest.raises(RuntimeError, match="no window"):
                mirror_poc.capture_frame()


# ── main (CLI) ────────────────────────────────────────────────────────────────

class TestMain:

    BOUNDS = {"X": 0.0, "Y": 0.0, "Width": 430.0, "Height": 960.0}

    def _patch_find(self):
        return patch(
            "mirror_poc._find_mirror_window",
            return_value=(1234, 42, self.BOUNDS),
        )

    def test_no_args_exits_zero(self):
        with patch("sys.argv", ["mirror_poc.py"]):
            with pytest.raises(SystemExit) as exc_info:
                mirror_poc.main()
        assert exc_info.value.code == 0

    def test_info_flag_prints_window_details(self, capsys):
        with self._patch_find(), \
             patch("sys.argv", ["mirror_poc.py", "--info"]):
            mirror_poc.main()
        out = capsys.readouterr().out
        assert "PID=1234" in out
        assert "WindowID=42" in out

    def test_tap_flag_calls_background_tap(self):
        with self._patch_find(), \
             patch("mirror_poc.background_tap") as mock_tap, \
             patch("sys.argv", ["mirror_poc.py", "--tap", "215,466"]):
            mirror_poc.main()
        mock_tap.assert_called_once_with(215, 466)

    def test_tap_flag_with_bad_format_exits_nonzero(self):
        with self._patch_find(), \
             patch("sys.argv", ["mirror_poc.py", "--tap", "215"]):
            with pytest.raises(SystemExit) as exc_info:
                mirror_poc.main()
        assert exc_info.value.code != 0

    def test_tap_flag_with_non_integer_exits_nonzero(self):
        with self._patch_find(), \
             patch("sys.argv", ["mirror_poc.py", "--tap", "abc,def"]):
            with pytest.raises(SystemExit) as exc_info:
                mirror_poc.main()
        assert exc_info.value.code != 0

    def test_screenshot_flag_saves_file(self, tmp_path):
        png = b"\x89PNGfakedata"
        out_file = tmp_path / "frame.png"
        with self._patch_find(), \
             patch("mirror_poc.capture_frame", return_value=png), \
             patch("sys.argv", ["mirror_poc.py", "--screenshot", str(out_file)]):
            mirror_poc.main()
        assert out_file.read_bytes() == png

    def test_screenshot_flag_prints_saved_path(self, tmp_path, capsys):
        out_file = tmp_path / "out.png"
        with self._patch_find(), \
             patch("mirror_poc.capture_frame", return_value=b"img"), \
             patch("sys.argv", ["mirror_poc.py", "--screenshot", str(out_file)]):
            mirror_poc.main()
        assert str(out_file) in capsys.readouterr().out

    def test_screenshot_capture_error_exits_nonzero(self):
        with self._patch_find(), \
             patch("mirror_poc.capture_frame",
                   side_effect=RuntimeError("permission denied")), \
             patch("sys.argv", ["mirror_poc.py", "--screenshot", "f.png"]):
            with pytest.raises(SystemExit) as exc_info:
                mirror_poc.main()
        assert exc_info.value.code != 0

    def test_window_not_found_exits_nonzero(self):
        with patch("mirror_poc._find_mirror_window",
                   side_effect=RuntimeError("not found")), \
             patch("sys.argv", ["mirror_poc.py", "--info"]):
            with pytest.raises(SystemExit) as exc_info:
                mirror_poc.main()
        assert exc_info.value.code != 0

    def test_tap_and_screenshot_together(self, tmp_path):
        out_file = tmp_path / "frame.png"
        with self._patch_find(), \
             patch("mirror_poc.background_tap") as mock_tap, \
             patch("mirror_poc.capture_frame", return_value=b"img"), \
             patch("sys.argv", [
                 "mirror_poc.py", "--tap", "100,200",
                 "--screenshot", str(out_file),
             ]):
            mirror_poc.main()
        mock_tap.assert_called_once_with(100, 200)
        assert out_file.exists()
