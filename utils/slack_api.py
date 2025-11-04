import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

# Slack practical limits
SECTION_SAFE_LIMIT = 2900   # keep under Slack's ~3000 char soft cap for mrkdwn
FALLBACK_LIMIT      = 1900  # for notifications / a11y
MAX_BLOCKS          = 50    # Slack hard cap

def _chunk(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i+size]

def send_message(
    client: WebClient,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
    user_id: str | None = None,
    export_pdf: bool = False,
    show_thumbs_up_feedback: bool = False,
    show_thumbs_down_feedback: bool = False,
    title: str | None = None,
):
    """
    Posts one message with ALL content inline (no file uploads).
    Long bodies are split across multiple section blocks under Slack's per-block limits.
    """

    try:
        blocks: list[dict] = []

        if title:
            blocks.append({"type": "header", "text": {"type": "plain_text", "text": title[:150], "emoji": False}})

        # ---------- Main body: MULTI-SECTION inline strategy (no file uploads) ----------
        # Reserve room for actions/feedback blocks by trimming body sections if needed.
        # We'll fill sections first, then optionally append actions.
        body_sections = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": part}
            }
            for part in _chunk(text, SECTION_SAFE_LIMIT)
        ]

        # Respect the MAX_BLOCKS limit
        # Keep space for at most 1 actions block + optional feedback blocks later
        reserved = 1  # actions
        if show_thumbs_up_feedback or show_thumbs_down_feedback:
            # each feedback adds 2 blocks (title + buttons)
            reserved += 2

        allowed_body_blocks = max(0, MAX_BLOCKS - len(blocks) - reserved)
        if len(body_sections) > allowed_body_blocks:
            # Truncate and add a note at the end
            body_sections = body_sections[:allowed_body_blocks]
            # Try to append an ellipsis to last section
            last = body_sections[-1]["text"]["text"]
            if len(last) <= SECTION_SAFE_LIMIT - 1:
                body_sections[-1]["text"]["text"] = last + "…"

        blocks.extend(body_sections)

        # ---------- Actions: thumbs + export + translate ----------
        thumbs = [
            {"type": "button", "text": {"type":"plain_text","text":"👍"}, "value":"thumbs_up",   "action_id":"vote_up"},
            {"type": "button", "text": {"type":"plain_text","text":"👎"}, "value":"thumbs_down", "action_id":"vote_down"},
        ]
        if export_pdf:
            thumbs.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Export to PDF"},
                "action_id": "export_pdf",
                "value": "export_pdf",
            })

        if not (show_thumbs_up_feedback or show_thumbs_down_feedback):
            if len(blocks) < MAX_BLOCKS:
                blocks.append({"type": "actions", "elements": thumbs})

        if show_thumbs_up_feedback and len(blocks) <= MAX_BLOCKS - 2:
            options = [
                "Accurate information",
                "Followed instructions perfectly",
                "Showcased creativity",
                "Positive attitude",
                "Attention to detail",
                "Thorough explanation",
                "Tell Us More",
            ]
            blocks.extend([
                {
                    "type": "section",
                    "block_id": "thumbs_up_feedback",
                    "text": {"type": "mrkdwn", "text": "*What did you like about Ask-Support Bot?*"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": label},
                            "value": label,
                            "action_id": f"thumbs_up_feedback_select_{i}",
                            "style": "primary",
                        } for i, label in enumerate(options)
                    ],
                },
            ])

        if show_thumbs_down_feedback and len(blocks) <= MAX_BLOCKS - 2:
            options = [
                "Don't like the style",
                "Too verbose",
                "Not helpful",
                "Not factually correct",
                "Didn't fully follow instructions",
                "Refused when it shouldn't have",
                "Tell Us More",
            ]
            blocks.extend([
                {
                    "type": "section",
                    "block_id": "thumbs_down_feedback",
                    "text": {"type": "mrkdwn", "text": "*What didn't resonate about Ask-Support Bot?*"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": label},
                            "value": label,
                            "action_id": f"thumbs_down_feedback_select_{i}",
                            "style": "danger",
                        } for i, label in enumerate(options)
                    ],
                },
            ])

        if export_pdf and len(blocks) < MAX_BLOCKS:
            blocks.append({
                "type": "actions",
                "block_id": "translate_controls",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "select_language",
                        "placeholder": {"type": "plain_text", "text": "Select language"},
                        "options": [
                            {"text": {"type":"plain_text","text":"Japanese"}, "value": "ja"},
                            {"text": {"type":"plain_text","text":"Spanish"},  "value": "es"},
                            {"text": {"type":"plain_text","text":"French"},   "value": "fr"},
                            {"text": {"type":"plain_text","text":"Chinese (Simplified)"}, "value": "zh"},
                        ],
                    },
                    {
                        "type": "button",
                        "action_id": "translate_button",
                        "text": {"type": "plain_text", "text": "Translate"},
                        "style": "primary",
                        "value": "translate_now",
                    },
                ],
            })

        # Fallback text (for notifications/a11y)
        fallback = (text[:FALLBACK_LIMIT] + "…") if len(text) > FALLBACK_LIMIT else text

        resp = client.chat_postMessage(
            channel=channel_id,
            text=fallback,
            blocks=blocks[:MAX_BLOCKS],
            thread_ts=thread_ts,
        )
        logger.info(f"Message sent to {channel_id} (thread {thread_ts or 'new'})")
        return resp

    except SlackApiError as e:
        logger.error(f"Failed to send Slack message: {e.response.get('error')}")
        raise
    except Exception:
        logger.exception("Unexpected error sending message to Slack")
        raise
