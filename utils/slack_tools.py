# utils/slack_tools.py

import time
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

# Cache user names with a 24 h TTL
_user_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 24 * 3600  # seconds

def get_user_name(client: WebClient, user_id: str) -> str:
    """
    Fetch and cache the display name for a user via the passed-in WebClient.
    """
    now = time.time()
    if user_id in _user_cache:
        name, ts = _user_cache[user_id]
        if now - ts < CACHE_TTL:
            return name

    try:
        resp = client.users_info(user=user_id)
        profile = resp["user"].get("profile", {})
        name = profile.get("display_name") or profile.get("real_name") or user_id
    except SlackApiError as e:
        logger.warning(f"Slack API users.info error for {user_id}: {e.response['error']}")
        name = user_id
    except Exception:
        logger.exception(f"Failed to fetch user info for {user_id}")
        name = user_id

    _user_cache[user_id] = (name, now)
    return name

def fetch_slack_thread(client: WebClient, channel_id: str, thread_ts: str) -> list[dict]:
    """
    Retrieve all messages in a thread via the passed-in WebClient.
    """
    try:
        resp = client.conversations_replies(channel=channel_id, ts=thread_ts, limit=1000)
        messages = resp.get("messages", [])
        return messages
    except SlackApiError as e:
        err = e.response["error"]
        logger.error(f"Slack API conversations.replies error for {channel_id}@{thread_ts}: {err}")
        raise RuntimeError(f"Error fetching thread: {err}")
    except Exception:
        logger.exception(f"Failed to fetch Slack thread {channel_id}@{thread_ts}")
        raise
