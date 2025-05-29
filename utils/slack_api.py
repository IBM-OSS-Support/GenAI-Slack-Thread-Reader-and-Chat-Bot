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
    user_id: str = None,
    export_pdf: bool = False,   # ← boolean flag
) -> None:
    # … your DM‐open logic …

    thumbs = [
        { "type": "button", "text": {"type":"plain_text","text":"👍"}, "value":"thumbs_up",   "action_id":"vote_up" },
        { "type": "button", "text": {"type":"plain_text","text":"👎"}, "value":"thumbs_down", "action_id":"vote_down" },
    ]

    if export_pdf:
        thumbs.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Export to PDF"},
            "action_id": "export_pdf",
            "value": text,
        })

    blocks = [
        {"type":"section", "text":{"type":"mrkdwn","text":text}},
        {"type":"actions", "elements": thumbs},
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
