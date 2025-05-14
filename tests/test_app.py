import sys, os
# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest
import time
from app import (
    resolve_user_mentions,
    get_channel_name,
    track_usage,
    get_bot_stats,
    _last_activity,
    _active_sessions,
    _unique_users,
    _command_counts,
    SLACK_BOT_TOKEN
)
import requests

class DummyResponse:
    def __init__(self, ok, name=None):
        self._data = {"ok": ok}
        if name:
            self._data["channel"] = {"name": name}
    def raise_for_status(self):
        pass
    def json(self):
        return self._data

@pytest.fixture(autouse=True)
def clear_state():
    # Clear global state before each test
    _last_activity.clear()
    _active_sessions.clear()
    _unique_users.clear()
    _command_counts.clear()
    yield
    _last_activity.clear()
    _active_sessions.clear()
    _unique_users.clear()
    _command_counts.clear()

def test_resolve_user_mentions(monkeypatch):
    # Mock get_user_name to return predictable names
    monkeypatch.setattr("app.get_user_name", lambda uid: "alice" if uid.startswith("U") else "bob")
    text = "Hi <@U12345678>, also ping @W87654321 and malformed @<U12345678>"
    result = resolve_user_mentions(text)
    assert "@alice" in result
    assert "@bob" in result
    assert "@alice" in result  # malformed case handled

def test_get_channel_name_success(monkeypatch):
    # Mock requests.get to simulate Slack API success
    def fake_get(url, headers, params, timeout):
        assert url.endswith("conversations.info")
        assert headers["Authorization"] == f"Bearer {SLACK_BOT_TOKEN}"
        assert params == {"channel": "C123CHAN"}
        return DummyResponse(ok=True, name="general")

    monkeypatch.setattr(requests, "get", fake_get)
    channel_name = get_channel_name("C123CHAN")
    assert channel_name == "#general"

def test_get_channel_name_failure(monkeypatch):
    # Simulate API failure
    def fake_get_fail(url, headers, params, timeout):
        raise requests.RequestException("failure")

    monkeypatch.setattr(requests, "get", fake_get_fail)
    channel_name = get_channel_name("C999ZZZ")
    assert channel_name == "#C999ZZZ"

def test_track_usage_and_stats():
    # Initially no users or sessions
    assert get_bot_stats().startswith("📊 Bot Usage Stats:")
    # Simulate usage
    now = time.time()
    track_usage("U1", "ts1")
    time.sleep(0.01)
    track_usage("U2", "ts2")
    # Check state
    assert len(_unique_users) == 2
    stats = get_bot_stats()
    assert "Unique users: 2" in stats
    assert "Live sessions: 2" in stats
def test_session_expiration():
    # Simulate session expiration edge
    # Directly manipulate _last_activity
    _last_activity["ts_old"] = time.time() - 1000
    # Expiration threshold = 600 seconds by default
    stats = get_bot_stats()
    assert "Live sessions: 0" in stats
