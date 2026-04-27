"""
Unit tests for sequence.py.

All WDA network I/O is mocked at the boundary; no real device required.
"""

import textwrap
import threading
import time
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import sequence
from sequence import Tap, Swipe, Wait, load_preset, run_sequence, _interruptible_sleep, _keyboard_listener, _parse_value, _resolve


# ── load_preset ───────────────────────────────────────────────────────────────

class TestLoadPreset:

    def _write_preset(self, tmp_path, name, content):
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / f"{name}.yaml").write_text(textwrap.dedent(content))
        return preset_dir

    def test_loads_tap_step(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: tap\n  x: 100\n  y: 200\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Tap(100, 200)]

    def test_loads_swipe_step_with_defaults(self, tmp_path):
        yaml = "- type: swipe\n  x1: 10\n  y1: 20\n  x2: 30\n  y2: 40\n"
        d = self._write_preset(tmp_path, "p", yaml)
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Swipe(10, 20, 30, 40, duration_ms=500)]

    def test_loads_swipe_step_with_custom_duration(self, tmp_path):
        yaml = "- type: swipe\n  x1: 10\n  y1: 20\n  x2: 30\n  y2: 40\n  duration_ms: 800\n"
        d = self._write_preset(tmp_path, "p", yaml)
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps[0].duration_ms == 800

    def test_loads_wait_step(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: wait\n  ms: 1500\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Wait(1500)]

    def test_loads_mixed_sequence(self, tmp_path):
        yaml = (
            "- type: tap\n  x: 1\n  y: 2\n"
            "- type: wait\n  ms: 500\n"
            "- type: swipe\n  x1: 3\n  y1: 4\n  x2: 5\n  y2: 6\n"
        )
        d = self._write_preset(tmp_path, "p", yaml)
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Tap(1, 2), Wait(500), Swipe(3, 4, 5, 6)]

    def test_raises_file_not_found(self, tmp_path):
        with patch.object(sequence, "PRESETS_DIR", tmp_path):
            with pytest.raises(FileNotFoundError, match="Preset not found"):
                load_preset("nonexistent")

    def test_raises_on_unknown_step_type(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: jump\n  x: 1\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            with pytest.raises(ValueError, match="unknown type 'jump'"):
                load_preset("p")

    def test_raises_when_root_is_not_list(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "type: tap\nx: 1\ny: 2\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            with pytest.raises(ValueError, match="must be a YAML list"):
                load_preset("p")

    def test_loads_tap_with_range_x(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: tap\n  x: [680, 720]\n  y: 400\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Tap(x=(680, 720), y=400)]

    def test_loads_swipe_with_range_duration(self, tmp_path):
        yaml = "- type: swipe\n  x1: 10\n  y1: 20\n  x2: 30\n  y2: 40\n  duration_ms: [250, 400]\n"
        d = self._write_preset(tmp_path, "p", yaml)
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps[0].duration_ms == (250, 400)

    def test_loads_wait_with_range_ms(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: wait\n  ms: [500, 1500]\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            steps = load_preset("p")
        assert steps == [Wait(ms=(500, 1500))]

    def test_raises_on_range_with_wrong_element_count(self, tmp_path):
        d = self._write_preset(tmp_path, "p", "- type: tap\n  x: [1, 2, 3]\n  y: 0\n")
        with patch.object(sequence, "PRESETS_DIR", d):
            with pytest.raises(ValueError, match="exactly 2 elements"):
                load_preset("p")

    def test_brawl_stars_preset_is_valid(self):
        steps = load_preset("brawl_stars")
        assert len(steps) > 0
        types = {type(s) for s in steps}
        assert types <= {Tap, Swipe, Wait}


# ── _parse_value / _resolve ───────────────────────────────────────────────────

class TestParseValue:

    def test_scalar_int(self):
        assert _parse_value(400) == 400

    def test_scalar_string_int(self):
        assert _parse_value("400") == 400

    def test_two_element_list(self):
        assert _parse_value([100, 200]) == (100, 200)

    def test_raises_on_one_element_list(self):
        with pytest.raises(ValueError, match="exactly 2 elements"):
            _parse_value([100])

    def test_raises_on_three_element_list(self):
        with pytest.raises(ValueError, match="exactly 2 elements"):
            _parse_value([1, 2, 3])


class TestResolve:

    def test_scalar_passthrough(self):
        assert _resolve(42) == 42

    def test_range_samples_within_bounds(self):
        results = {_resolve((100, 200)) for _ in range(200)}
        assert all(100 <= v <= 200 for v in results)

    def test_range_lo_equals_hi(self):
        assert _resolve((7, 7)) == 7

    def test_range_uses_random_randint(self):
        with patch("sequence.random.randint", return_value=150) as mock_ri:
            result = _resolve((100, 200))
        mock_ri.assert_called_once_with(100, 200)
        assert result == 150


# ── run_sequence ──────────────────────────────────────────────────────────────

class TestRunSequence:

    def setup_method(self):
        sequence._pause_event.clear()

    def test_executes_tap(self):
        with patch("sequence.tap") as mock_tap:
            run_sequence([Tap(10, 20)], "sess", count=1)
        mock_tap.assert_called_once_with("sess", 10, 20)

    def test_executes_swipe(self):
        with patch("sequence.swipe") as mock_swipe:
            run_sequence([Swipe(1, 2, 3, 4, 300)], "sess", count=1)
        mock_swipe.assert_called_once_with("sess", 1, 2, 3, 4, 300)

    def test_executes_wait(self):
        slept = []
        with patch("sequence._interruptible_sleep", side_effect=lambda s: slept.append(s)):
            run_sequence([Wait(750)], "sess", count=1)
        assert slept == [0.75]

    def test_executes_steps_in_order(self):
        calls = []
        with patch("sequence.tap", side_effect=lambda s, x, y: calls.append(("tap", x, y))), \
             patch("sequence.swipe", side_effect=lambda s, *a: calls.append(("swipe",) + a)), \
             patch("sequence._interruptible_sleep", side_effect=lambda s: calls.append(("wait", s))):
            run_sequence([Tap(1, 2), Wait(500), Swipe(3, 4, 5, 6)], "sess", count=1)
        assert calls == [("tap", 1, 2), ("wait", 0.5), ("swipe", 3, 4, 5, 6, 500)]

    def test_repeats_count_times(self):
        tap_calls = []
        with patch("sequence.tap", side_effect=lambda s, x, y: tap_calls.append((x, y))):
            run_sequence([Tap(1, 2)], "sess", count=3)
        assert tap_calls == [(1, 2), (1, 2), (1, 2)]

    def test_count_zero_loops_until_interrupted(self):
        tap_calls = {"n": 0}

        def tap_effect(s, x, y):
            tap_calls["n"] += 1
            if tap_calls["n"] >= 5:
                raise KeyboardInterrupt

        with patch("sequence.tap", side_effect=tap_effect):
            with pytest.raises(KeyboardInterrupt):
                run_sequence([Tap(1, 2)], "sess", count=0)

        assert tap_calls["n"] == 5

    def test_range_tap_resolves_per_cycle(self):
        tap_calls = []
        with patch("sequence.tap", side_effect=lambda s, x, y: tap_calls.append((x, y))), \
             patch("sequence.random.randint", side_effect=[10, 20, 30, 40]):
            run_sequence([Tap(x=(1, 5), y=(6, 9))], "sess", count=2)
        assert tap_calls == [(10, 20), (30, 40)]

    def test_raises_on_empty_steps(self):
        with pytest.raises(ValueError, match="steps must not be empty"):
            run_sequence([], "sess", count=1)

    def test_pauses_between_steps(self):
        sequence._pause_event.set()
        unpaused = threading.Event()

        def tap_effect(s, x, y):
            unpaused.set()

        def unpause_thread():
            time.sleep(0.05)
            sequence._pause_event.clear()

        threading.Thread(target=unpause_thread, daemon=True).start()
        with patch("sequence.tap", side_effect=tap_effect):
            run_sequence([Tap(1, 2)], "sess", count=1)

        assert unpaused.is_set()


# ── _keyboard_listener ───────────────────────────────────────────────────────

class TestKeyboardListener:

    def setup_method(self):
        sequence._pause_event.clear()

    def _run(self, keys):
        with patch.object(sys.stdin, "read", side_effect=keys):
            _keyboard_listener()

    def test_space_pauses(self):
        self._run([" ", ""])
        assert sequence._pause_event.is_set()

    def test_p_pauses(self):
        self._run(["p", ""])
        assert sequence._pause_event.is_set()

    def test_space_toggles_resume(self):
        self._run([" ", " ", ""])
        assert not sequence._pause_event.is_set()

    def test_other_keys_ignored(self):
        self._run(["x", "q", ""])
        assert not sequence._pause_event.is_set()

    def test_eof_breaks_loop(self):
        # Empty string from read() signals EOF; listener must return cleanly.
        self._run([""])  # immediate EOF — should not spin


# ── _interruptible_sleep ──────────────────────────────────────────────────────

class TestInterruptibleSleep:

    def setup_method(self):
        sequence._pause_event.clear()

    def test_sleeps_approximately_correct_duration(self):
        start = time.monotonic()
        _interruptible_sleep(0.1)
        assert 0.08 <= time.monotonic() - start <= 0.5

    def test_extends_sleep_while_paused(self):
        sequence._pause_event.set()
        resumed = threading.Event()

        def unpause():
            time.sleep(0.05)
            sequence._pause_event.clear()
            resumed.set()

        threading.Thread(target=unpause, daemon=True).start()
        start = time.monotonic()
        _interruptible_sleep(0.01)
        assert resumed.is_set()
        assert time.monotonic() - start >= 0.05


# ── main (CLI) ────────────────────────────────────────────────────────────────

class TestMain:

    def _wda_ok(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {"ready": True}}
        return resp

    def test_exits_on_missing_preset(self):
        with patch("sys.argv", ["sequence.py", "--preset", "nonexistent_xyz"]), \
             patch.object(sequence, "PRESETS_DIR", Path("/nonexistent")):
            with pytest.raises(SystemExit) as exc:
                sequence.main()
        assert exc.value.code != 0

    def test_exits_when_wda_unreachable(self, tmp_path):
        (tmp_path / "p.yaml").write_text("- type: tap\n  x: 1\n  y: 2\n")
        with patch("sys.argv", ["sequence.py", "--preset", "p"]), \
             patch.object(sequence, "PRESETS_DIR", tmp_path), \
             patch("requests.get", side_effect=ConnectionRefusedError):
            with pytest.raises(SystemExit) as exc:
                sequence.main()
        assert exc.value.code != 0

    def test_exits_when_wda_not_ready(self, tmp_path):
        (tmp_path / "p.yaml").write_text("- type: tap\n  x: 1\n  y: 2\n")
        resp = MagicMock()
        resp.json.return_value = {"value": {"ready": False}}
        with patch("sys.argv", ["sequence.py", "--preset", "p"]), \
             patch.object(sequence, "PRESETS_DIR", tmp_path), \
             patch("requests.get", return_value=resp):
            with pytest.raises(SystemExit) as exc:
                sequence.main()
        assert exc.value.code != 0

    def test_runs_sequence_with_count(self, tmp_path):
        (tmp_path / "p.yaml").write_text("- type: tap\n  x: 1\n  y: 2\n")
        tap_calls = []
        with patch("sys.argv", ["sequence.py", "--preset", "p", "--count", "2"]), \
             patch.object(sequence, "PRESETS_DIR", tmp_path), \
             patch("requests.get", return_value=self._wda_ok()), \
             patch("sequence.get_or_create_session", return_value="sess"), \
             patch("sequence.tap", side_effect=lambda s, x, y: tap_calls.append((x, y))), \
             patch("termios.tcgetattr", return_value=[]), \
             patch("termios.tcsetattr"), \
             patch("tty.setcbreak"), \
             patch("threading.Thread"):
            sequence.main()
        assert tap_calls == [(1, 2), (1, 2)]
