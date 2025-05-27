# utils/slack_api.py

import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

def send_message(
    client: WebClient,
    channel_id: str,
    text: str,
    thread_ts: str = None,
    user_id: str = None,            # ← new
) -> None:
    logging.debug(f"send_message called with channel_id={channel_id}, text={text}, thread_ts={thread_ts}, user_id={user_id}")
    """
    Send a message (with 👍/👎 buttons) using the passed-in WebClient.
    If it's a DM channel, we open (or re-open) the IM first.
    """
    # 1) If this looks like an IM channel *or* we have a user_id, ensure the bot can write there
    if channel_id.startswith("D") and user_id:
        try:
            open_resp = client.conversations_open(users=user_id)
            channel_id = open_resp["channel"]["id"]
        except SlackApiError as e:
            logger.error(f"conversations.open failed ({user_id}): {e.response['error']}")
            return

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "👍"}, "value": "thumbs_up", "action_id": "vote_up"},
                {"type": "button", "text": {"type": "plain_text", "text": "👎"}, "value": "thumbs_down", "action_id": "vote_down"},
            ],
        },
    ]
    try:
        client.chat_postMessage(
            channel=channel_id,
            text=text,    # fallback text
            blocks=blocks,
            thread_ts=thread_ts,
        )
        logger.info(f"Message sent to {channel_id} (thread {thread_ts or 'new'})")
    except SlackApiError as e:
        logger.error(f"Failed to send Slack message: {e.response['error']}")
    except Exception:
        logger.exception("Unexpected error sending message to Slack")
