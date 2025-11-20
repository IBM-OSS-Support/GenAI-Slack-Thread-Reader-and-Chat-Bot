import json
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import os
import time
import dateparser
from datetime import datetime
from helper.llm_utils import llama_infer
from db import get_user_tasks

logger = logging.getLogger(__name__)

client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# Cache to avoid redundant API calls
USER_CACHE = {}
DM_CHANNEL_CACHE = {}

def is_duplicate_task(user_id, task_description):
    """
    Return True if same user already has a task with similar text.
    """
    existing = get_user_tasks(user_id)

    task_lower = task_description.lower()

    for t in existing:
        desc = t[1].lower()   # index 1 = task_description
        if task_lower == desc or task_lower in desc or desc in task_lower:
            return True
    return False

def parse_natural_deadline(deadline_text):
    if not deadline_text or deadline_text.lower() == "no deadline":
        return None
    
    parsed = dateparser.parse(deadline_text, settings={"PREFER_DATES_FROM": "future"})
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    
    return None

def summarize_task_llm(text):
    prompt = f"""
You are an assistant that rewrites messy or incomplete text into a **single, clear, actionable task sentence**.

Your rules:
- Produce **only ONE short sentence**.
- No bullets, no numbering, no lists.
- No extra commentary.
- Fix grammar and make it professional.
- Do NOT add additional steps or expand it into a multi-step process.
- Only rewrite the user's text into a clean, concise action.

Original text:
{text}

Respond with ONLY the cleaned one-line task.
"""
    result = llama_infer(prompt).strip()

    # Safety: keep only the first non-empty line
    first_line = next((line.strip() for line in result.split("\n") if line.strip()), "")

    # Remove bullet symbols or numbering if any survived
    cleaned = first_line.lstrip("*-1234567890. ").strip()

    return cleaned


def slack_api_call_with_retry(method, **kwargs):
    """Wrapper to handle Slack rate limits gracefully."""
    while True:
        try:
            return method(**kwargs)
        except SlackApiError as e:
            if e.response["error"] == "ratelimited":
                retry_after = int(e.response.headers.get("Retry-After", 20))
                logger.warning(f"Rate limited. Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            else:
                logger.error(f"Slack API Error: {e.response['error']}")
                return None

def get_user_id_by_name_part(name_part):
    """Get a user's ID by partial match (real or display name)."""
    if name_part in USER_CACHE:
        return USER_CACHE[name_part]

    response = slack_api_call_with_retry(client.users_list)
    if not response:
        return None, None

    name_part = name_part.lower()
    for user in response["members"]:
        if user.get("deleted"):
            continue
        profile = user.get("profile", {})
        real_name = profile.get("real_name", "").lower()
        display_name = profile.get("display_name", "").lower()
        if name_part in real_name or name_part in display_name:
            USER_CACHE[name_part] = (user["id"], real_name or display_name)
            return user["id"], real_name or display_name

    return None, None

def get_dm_channel_between(user1_id, user2_id):
    """Find existing DM or MPIM channel between two users."""
    cache_key = f"{user1_id}-{user2_id}"
    if cache_key in DM_CHANNEL_CACHE:
        return DM_CHANNEL_CACHE[cache_key]

    response = slack_api_call_with_retry(client.conversations_list, types="im,mpim", limit=1000)
    if not response:
        return None

    for channel in response["channels"]:
        members_resp = slack_api_call_with_retry(client.conversations_members, channel=channel["id"])
        if not members_resp:
            continue
        members = members_resp.get("members", [])
        if user1_id in members and user2_id in members:
            DM_CHANNEL_CACHE[cache_key] = channel["id"]
            return channel["id"]

    return None

def fetch_dm_messages_between_users(user1_name, user2_name):
    """Fetch DM messages between two users."""
    user1_id, _ = get_user_id_by_name_part(user1_name)
    user2_id, _ = get_user_id_by_name_part(user2_name)
    if not user1_id or not user2_id:
        logger.error(f"Could not find user IDs for {user1_name} or {user2_name}.")
        return []

    channel_id = get_dm_channel_between(user1_id, user2_id)
    if not channel_id:
        logger.error(f"No DM channel found between {user1_name} and {user2_name}.")
        return []

    messages = []
    cursor = None

    while True:
        response = slack_api_call_with_retry(client.conversations_history, channel=channel_id, limit=200, cursor=cursor)
        if not response:
            break
        messages.extend(response["messages"])
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    messages = messages[::-1]  # chronological order
    logger.info(f"Fetched {len(messages)} messages between {user1_name} and {user2_name}.")
    return messages

def post_action_items_with_checkboxes_dm(client, channel, tasks, thread_ts=None, context="dm"):
    """
    Post action items with interactive checkboxes for DM context.
    """
    try:
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📌 Extracted Tasks ({context})"}
            }
        ]

        for idx, t in enumerate(tasks):
            task_text = (t.get("action") or t.get("task") or t.get("description", "")).strip()
            responsible = (t.get("responsible") or "").strip()
            deadline = (t.get("deadline") or "").strip() or "No Deadline"

            # Truncate text to be safe for Slack limits
            safe_task_text = (task_text[:250] + "…") if len(task_text) > 250 else task_text
            safe_responsible = responsible[:50]
            safe_deadline = deadline[:50]

            # ✔ FIX: Correct value format for parsing in action handler
            # Format: responsible|task_description|deadline
            value_str = f"{safe_responsible}|{safe_task_text}|{safe_deadline}"

            section_text = f"*{safe_task_text}*"
            if safe_responsible:
                section_text += f"\nResponsible: *{safe_responsible}*"
            if safe_deadline:
                section_text += f"\nDeadline: {safe_deadline}"

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": section_text},
                "accessory": {
                    "type": "checkboxes",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": " Claim Task"},
                            "value": value_str
                        }
                    ],
                    "action_id": "claim_task_action"
                }
            })

        response = client.chat_postMessage(
            channel=channel,
            text="Extracted Tasks",
            blocks=blocks,
            thread_ts=thread_ts,
        )

        logger.info(f"Tasks posted successfully in {context}.")
        return response

    except SlackApiError as e:
        logger.error(f"Slack API Error while posting tasks: {e.response['error']}")
        logger.debug(f"Failed blocks payload:\n{json.dumps(blocks, indent=2)}")
    except Exception as e:
        logger.error(f"Error posting tasks: {str(e)}", exc_info=True)

