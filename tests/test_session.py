"""
Unit tests for src/_session.py.

All network I/O is mocked at the boundary so no real device or WDA instance
is required.
"""

import pytest
import requests
from unittest.mock import patch, MagicMock

import _session


class TestGetOrCreateSession:

    def test_returns_top_level_session_id(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": "abc123"}
        with patch("requests.post", return_value=resp) as mock_post:
            sid = _session.get_or_create_session()
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
            assert _session.get_or_create_session() == "xyz789"

    def test_top_level_none_falls_back_to_value_dict(self):
        resp = MagicMock()
        resp.json.return_value = {"sessionId": None, "value": {"sessionId": "fallback"}}
        with patch("requests.post", return_value=resp):
            assert _session.get_or_create_session() == "fallback"

    def test_raises_when_session_id_absent(self):
        resp = MagicMock()
        resp.json.return_value = {"value": {}}
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="Could not create WDA session"):
                _session.get_or_create_session()

    def test_propagates_connection_error(self):
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                _session.get_or_create_session()

    def test_propagates_timeout(self):
        with patch("requests.post", side_effect=requests.Timeout("timed out")):
            with pytest.raises(requests.Timeout):
                _session.get_or_create_session()
