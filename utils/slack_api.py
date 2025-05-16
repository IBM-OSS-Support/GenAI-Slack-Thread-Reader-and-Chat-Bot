from dotenv import load_dotenv
load_dotenv()

import os
import logging
import requests

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
if not SLACK_BOT_TOKEN:
    logger.error("🚨 SLACK_BOT_TOKEN is missing or empty!")

def send_message(channel_id: str, text: str, thread_ts: str = None) -> None:
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "text": text,
        "blocks": [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👍"},
                    "value": "thumbs_up",
                    "action_id": "vote_up"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👎"},
                    "value": "thumbs_down",
                    "action_id": "vote_down"
                }
            ]
        }
        ]
    }

    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=headers,
            json=payload,
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Failed to send Slack message: {data.get('error')}")
        else:
            logger.info(f"Message sent to {channel_id} (thread {thread_ts or 'new'})")
    except Exception:
        logger.exception("Error sending message to Slack")
