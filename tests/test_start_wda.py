"""
Unit tests for scripts/start_wda.py.

All subprocess calls and network I/O are mocked at the boundary.
"""

import pathlib
import sys
import time
import xml.etree.ElementTree as ET

import pytest
from unittest.mock import MagicMock, patch

# scripts/ is not on sys.path by default — add it so we can import start_wda
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
import start_wda


# ── load_env ───────────────────────────────────────────────────────────────────

class TestLoadEnv:

    def test_returns_udid_and_team(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('UDID="abc123"\nTEAM="XYZ789"\n')
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "abc123"
        assert result["TEAM"] == "XYZ789"

    def test_strips_double_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('UDID="quoted-id"\nTEAM="quoted-team"\n')
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "quoted-id"
        assert result["TEAM"] == "quoted-team"

    def test_strips_single_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("UDID='single-id'\nTEAM='single-team'\n")
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "single-id"
        assert result["TEAM"] == "single-team"

    def test_handles_unquoted_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("UDID=bare-id\nTEAM=bare-team\n")
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "bare-id"
        assert result["TEAM"] == "bare-team"

    def test_strips_inline_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('UDID=abc123 # my device\nTEAM=XYZ # work team\n')
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "abc123"
        assert result["TEAM"] == "XYZ"

    def test_ignores_blank_lines_and_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment\n\nUDID=u\n\n# another comment\nTEAM=t\n"
        )
        result = start_wda.load_env(env_file)
        assert result["UDID"] == "u"
        assert result["TEAM"] == "t"

    def test_raises_file_not_found_when_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=".env not found"):
            start_wda.load_env(tmp_path / "nonexistent.env")

    def test_raises_value_error_when_udid_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("TEAM=t\n")
        with pytest.raises(ValueError, match="UDID"):
            start_wda.load_env(env_file)

    def test_raises_value_error_when_team_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("UDID=u\n")
        with pytest.raises(ValueError, match="TEAM"):
            start_wda.load_env(env_file)

    def test_raises_value_error_when_udid_empty_string(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('UDID=""\nTEAM=t\n')
        with pytest.raises(ValueError, match="UDID"):
            start_wda.load_env(env_file)


# ── build_xctestrun ────────────────────────────────────────────────────────────

class TestBuildXctestrun:

    def test_creates_file_at_given_path(self, tmp_path):
        out = tmp_path / "test.xctestrun"
        start_wda.build_xctestrun("MYTEAM1", out)
        assert out.exists()

    def test_output_is_valid_xml(self, tmp_path):
        out = tmp_path / "test.xctestrun"
        start_wda.build_xctestrun("MYTEAM1", out)
        ET.parse(out)  # raises if invalid

    def test_embeds_team_id_in_bundle_identifiers(self, tmp_path):
        out = tmp_path / "test.xctestrun"
        start_wda.build_xctestrun("MYTEAM1", out)
        content = out.read_text()
        assert "com.MYTEAM1.WebDriverAgentRunner.xctrunner" in content

    def test_different_teams_produce_different_files(self, tmp_path):
        a = tmp_path / "a.xctestrun"
        b = tmp_path / "b.xctestrun"
        start_wda.build_xctestrun("TEAM_A", a)
        start_wda.build_xctestrun("TEAM_B", b)
        assert a.read_text() != b.read_text()

    def test_overwrites_existing_file(self, tmp_path):
        out = tmp_path / "test.xctestrun"
        out.write_text("old content")
        start_wda.build_xctestrun("NEWTEAM", out)
        assert "NEWTEAM" in out.read_text()
        assert "old content" not in out.read_text()


# ── wait_for_wda ───────────────────────────────────────────────────────────────

class TestWaitForWda:

    def test_returns_true_when_wda_responds_immediately(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.get", return_value=resp), \
             patch("time.sleep"):
            assert start_wda.wait_for_wda(timeout=5, interval=0.1) is True

    def test_returns_true_after_initial_failures(self):
        call_n = {"n": 0}

        def get_side_effect(url, timeout):
            call_n["n"] += 1
            if call_n["n"] < 3:
                raise ConnectionRefusedError("not up yet")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch("requests.get", side_effect=get_side_effect), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]):
            assert start_wda.wait_for_wda(timeout=10, interval=0.1) is True

    def test_returns_false_on_timeout(self):
        # monotonic sequence: deadline call → enter loop twice → exit loop
        # deadline = 0 + 5 = 5; 1 < 5 → run; 2 < 5 → run; 99 > 5 → exit
        with patch("requests.get", side_effect=ConnectionRefusedError), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 1, 2, 99]):
            assert start_wda.wait_for_wda(timeout=5, interval=0.1) is False

    def test_returns_false_when_status_code_is_not_200(self):
        resp = MagicMock()
        resp.status_code = 500
        # deadline = 0 + 5 = 5; 1 < 5 → run; 99 > 5 → exit
        with patch("requests.get", return_value=resp), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 1, 99]):
            assert start_wda.wait_for_wda(timeout=5, interval=0.1) is False

    def test_polls_correct_url(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch("requests.get", return_value=resp) as mock_get, \
             patch("time.sleep"):
            start_wda.wait_for_wda(url="http://127.0.0.1:8100", timeout=5, interval=0.1)
        mock_get.assert_called_with("http://127.0.0.1:8100/status", timeout=1)


# ── main / pkill ───────────────────────────────────────────────────────────────

class TestMainPkill:

    def test_pkill_uses_current_user_flag(self, tmp_path):
        """pkill must include -u <uid> so it never prompts for a password."""
        import os
        env_file = tmp_path / ".env"
        env_file.write_text("UDID=test-udid\nTEAM=TESTTEAM\n")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        with patch.object(start_wda.pathlib.Path, "parent", new_callable=lambda: property(lambda self: tmp_path)), \
             patch("start_wda.load_env", return_value={"UDID": "test-udid", "TEAM": "TESTTEAM"}), \
             patch("start_wda.build_xctestrun"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen", return_value=fake_proc), \
             patch("builtins.open", MagicMock()), \
             patch("time.sleep"), \
             patch("start_wda.wait_for_wda", return_value=True), \
             patch("sys.exit"):
            start_wda.main()

        pkill_calls = [c for c in calls if c and c[0] == "pkill"]
        assert pkill_calls, "pkill was never called"
        pkill_cmd = pkill_calls[0]
        assert "-u" in pkill_cmd, "pkill missing -u flag (could prompt for password)"
        assert str(os.getuid()) in pkill_cmd, "pkill -u should use current user's UID"
