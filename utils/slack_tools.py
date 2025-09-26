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
def _split_mrkdwn_for_slack(text: str, limit: int = 2900) -> list[str]:
    """
    Split mrkdwn into chunks that are safe for Slack section blocks (<= limit).
    Tries not to cut inside triple-backtick code fences.
    """
    if not text:
        return [""]

    chunks = []
    buf = []
    current_len = 0
    in_code = False

    # Split by lines so we can track ``` fences
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```") and stripped.count("```") == 1:
            in_code = not in_code

        if current_len + len(line) > limit:
            # If we’re in a code block, try to close/open to keep fences balanced
            if in_code:
                buf.append("\n```")
                chunks.append("".join(buf))
                buf = ["```" + "\n"]
                current_len = len(buf[0])
                # still too long? hard split
                if len(line) > limit:
                    # hard wrap the long line
                    start = 0
                    while start < len(line):
                        take = min(limit - current_len, len(line) - start)
                        buf.append(line[start:start+take])
                        current_len += take
                        start += take
                        if current_len >= limit:
                            buf.append("\n```")
                            chunks.append("".join(buf))
                            buf = ["```" + "\n"]
                            current_len = len(buf[0])
                    continue

            else:
                # close current chunk
                chunks.append("".join(buf))
                buf = []
                current_len = 0

        buf.append(line)
        current_len += len(line)

    if buf:
        # Close code fence if dangling
        if in_code and (not buf[-1].strip().endswith("```")):
            buf.append("\n```")
        chunks.append("".join(buf))

    # Safety: never return empty list
    return [c if c.strip() else " " for c in chunks] or [" "]
