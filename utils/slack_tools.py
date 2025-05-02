import time
import logging
import requests
import os

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
if not SLACK_BOT_TOKEN:
    logger.error("🚨 SLACK_BOT_TOKEN is missing or empty!")

# Cache user names with a 24 h TTL
_user_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 24 * 3600  # seconds

def get_user_name(user_id: str) -> str:
    now = time.time()
    if user_id in _user_cache:
        name, ts = _user_cache[user_id]
        if now - ts < CACHE_TTL:
            return name

    url = "https://slack.com/api/users.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"user": user_id}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            profile = data["user"].get("profile", {})
            name = profile.get("display_name") or profile.get("real_name") or user_id
        else:
            logger.warning(f"Slack API users.info error: {data.get('error')}")
            name = user_id
    except Exception:
        logger.exception(f"Failed to fetch user info for {user_id}")
        name = user_id

    _user_cache[user_id] = (name, now)
    return name

def fetch_slack_thread(channel_id: str, thread_ts: str) -> list[dict]:
    url = "https://slack.com/api/conversations.replies"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": channel_id, "ts": thread_ts}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            err = data.get("error", "unknown_error")
            logger.error(f"Slack API conversations.replies error: {err}")
            raise RuntimeError(f"Error fetching thread: {err}")
        return data.get("messages", [])
    except Exception:
        logger.exception("Failed to fetch Slack thread")
        raise
