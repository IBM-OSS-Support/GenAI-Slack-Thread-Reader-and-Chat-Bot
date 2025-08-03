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
    show_thumbs_up_feedback: bool = False,
    show_thumbs_down_feedback: bool = False
) -> None:
    # … your DM‐open logic …

    # Feedback options
    thumbs_up_feedback = [
        "Accurate information",
        "Followed instructions perfectly",
        "Showcased creativity",
        "Positive attitude",
        "Attention to detail",
        "Thorough explanation",
        "Tell Us More"
    ]

    thumbs_down_feedback = [
        "Don't like the style",
        "Too verbose",
        "Not helpful",
        "Not factually correct",
        "Didn't fully follow instructions",
        "Refused when it shouldn't have",
        "Tell Us More"
    ]

    thumbs = [
        {"type": "button", "text": {"type":"plain_text","text":"👍"}, "value":"thumbs_up",   "action_id":"vote_up"},
        {"type": "button", "text": {"type":"plain_text","text":"👎"}, "value":"thumbs_down", "action_id":"vote_down"},
    ]   

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    ]

    # Only show thumbs in the top-level message (i.e., not a threaded reply), and if not showing feedback
    if not show_thumbs_up_feedback and not show_thumbs_down_feedback:
        blocks.append({
            "type": "actions",
            "elements": thumbs
        })


    if show_thumbs_up_feedback:
        blocks.append({
            "type": "section",
            "block_id": "thumbs_up_feedback",
            "text": {"type": "mrkdwn", "text": "*What did you like about Ask-Support Bot?*"}
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option},
                    "value": option,
                    "action_id": f"thumbs_up_feedback_select_{i}",
                    "style": "primary"
                }
                for i, option in enumerate(thumbs_up_feedback)
            ]
        })
    elif show_thumbs_down_feedback:
        blocks.append({
            "type": "section",
            "block_id": "thumbs_down_feedback",
            "text": {"type": "mrkdwn", "text": "*What didn't resonate about Ask-Support Bot?*"}
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option},
                    "value": option,
                    "action_id": f"thumbs_down_feedback_select_{i}",
                    "style": "danger"
                }
                for i, option in enumerate(thumbs_down_feedback)
            ]
        })

    translate_controls = {
        "type": "actions",
        "block_id": "translate_controls",
        "elements": [
            {
                "type": "static_select",
                "action_id": "select_language",
                "placeholder": {
                    "type": "plain_text",
                    "text": "Select language"
                },
                "options": [
                    {"text": {"type": "plain_text", "text": "Japanese"}, "value": "ja"},
                    {"text": {"type": "plain_text", "text": "Spanish"}, "value": "es"},
                    {"text": {"type": "plain_text", "text": "French"},  "value": "fr"},
                    {"text": {"type": "plain_text", "text": "Chinese (Simplified)"}, "value": "zh"},
                    # …add more languages as needed
                ]
            },
            {
                "type": "button",
                "action_id": "translate_button",
                "text": {"type": "plain_text", "text": "Translate"},
                "style": "primary",
                "value": "translate_now"
            }
        ]
    }

    if export_pdf:
        thumbs.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Export to PDF"},
            "action_id": "export_pdf",
            "value": "export_pdf",       # ← short, fixed identifier
        })
    

    if export_pdf:
        blocks.append(translate_controls)
    try:
        response = client.chat_postMessage(
            channel=channel_id,
            text=text,    # fallback text
            blocks=blocks,
            thread_ts=thread_ts,
        )
        logger.info(f"Message sent to {channel_id} (thread {thread_ts or 'new'})")
        return response
    except SlackApiError as e:
        logger.error(f"Failed to send Slack message: {e.response['error']}")
    except Exception:
        logger.exception("Unexpected error sending message to Slack")
