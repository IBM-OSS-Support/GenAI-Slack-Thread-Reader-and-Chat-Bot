import os

# ✅ Inject fake env vars before importing app
os.environ["TEAM2_ID"] = "T1"
os.environ["TEAM1_BOT_TOKEN"] = "xoxb-fake-token"
os.environ["SLACK_APP_TOKEN"] = "xapp-fake"
os.environ["SLACK_SIGNING_SECRET"] = "secret"
os.environ["BOT_USER_ID"] = "U123"

import pytest
from unittest.mock import MagicMock
from slack_sdk.errors import SlackApiError
import app


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.conversations_info.return_value = {"channel": {"id": "C123"}}
    client.conversations_list.return_value = {
        "channels": [{"id": "C123", "name": "general"}]
    }
    return client


@pytest.fixture
def workspace_router(fake_client):
    router = app.WorkspaceRouter({"T1": "xoxb-fake-token"})
    router._clients["T1"] = fake_client
    return router


# -------------------- WorkspaceRouter Tests --------------------
def test_get_client_returns_same_instance(workspace_router, fake_client):
    c1 = workspace_router.get_client("T1")
    c2 = workspace_router.get_client("T1")
    assert c1 is c2


def test_get_client_fallback(workspace_router):
    client = workspace_router.get_client("UNKNOWN")
    assert client is workspace_router._clients["T1"]


def test_iter_clients_with_priority(workspace_router):
    items = list(workspace_router.iter_clients_with_priority("T1"))
    assert items[0][0] == "T1"


def test_find_channel_by_id(workspace_router):
    result = workspace_router.find_channel_anywhere("C123")
    assert result == ("T1", "C123")


def test_find_channel_by_name(workspace_router):
    result = workspace_router.find_channel_anywhere("general")
    assert result == ("T1", "C123")


def test_find_channel_not_found(workspace_router, fake_client):
    fake_client.conversations_list.return_value = {"channels": []}
    result = workspace_router.find_channel_anywhere("doesnotexist")
    assert result is None


def test_find_channel_handles_slack_error(workspace_router, fake_client):
    fake_client.conversations_info.side_effect = SlackApiError(
        "error", response={"ok": False, "error": "channel_not_found"}
    )
    result = workspace_router.find_channel_anywhere("C999")
    assert result is None


# -------------------- Health Check --------------------
def test_health_app_and_server_exist():
    assert hasattr(app, "health_app")
    assert callable(app.run_health_server)
