import json
import os
import logging
from datetime import datetime, timedelta, timezone
import time
import pytz
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError
from action_item_generator import ActionItemGenerator
import re
from db import check_existing_task, delete_task, get_user_tasks, save_task_to_db

load_dotenv()
from slack_sdk import WebClient

# Import your DM utilities
from helper.dm_utils import get_user_id_by_name_part, fetch_dm_messages_between_users, post_action_items_with_checkboxes_dm
from helper.llm_utils import extract_action_items_llm
from helper.utils import extract_deadline_from_text

client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
resp = client.auth_test()
print(resp)

seoul_tz = pytz.timezone('Asia/Seoul')

class SeoulFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        return dt.astimezone(seoul_tz)
    
    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('slack_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

for handler in logging.getLogger().handlers:
    handler.setFormatter(SeoulFormatter())

logger = logging.getLogger(__name__)


# haneesh:- use same environment variable names

MODEL_TYPE = os.getenv("MODEL_TYPE", "ollama")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

print("Bot token:", os.getenv("SLACK_BOT_TOKEN"))


try:
    BOT_USER_ID = app.client.auth_test()["user_id"]
    logger.info(f"Bot user ID: {BOT_USER_ID}")
except Exception as e:
    logger.error(f"Error fetching bot user ID: {str(e)}")
    BOT_USER_ID = None

action_item_generator = ActionItemGenerator(
    slack_app=app, 
    model_type=MODEL_TYPE,
    model_name=OLLAMA_MODEL
)

# Channel extraction functions
def get_channel_id(app, channel_name):
    """Find channel ID by name"""
    try:
        # Search public channels
        result = app.client.conversations_list(types="public_channel", limit=1000)
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]
        
        # Search private channels
        result = app.client.conversations_list(types="private_channel", limit=1000)
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]
                
        logger.warning(f"Channel not found: {channel_name}")
        return None
    except Exception as e:
        logger.error(f"Error finding channel: {str(e)}")
        return None

def extract_channel_history(channel_name, start_date, end_date):
    """Extract messages from a channel between date range"""
    try:
        channel_id = get_channel_id(app, channel_name)
        if not channel_id:
            return None, f"Channel '{channel_name}' not found"
        
        # Convert dates to timestamps
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(
            tzinfo=seoul_tz, 
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(
            tzinfo=seoul_tz,
            hour=23, minute=59, second=59, microsecond=999999
        )
        
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
        
        logger.info(f"Extracting messages from {channel_name} between {start_date} and {end_date}")
        logger.info(f"Timestamp range: {start_ts} to {end_ts}")
        
        messages = []
        cursor = None
        page = 1
        
        while True:
            try:
                params = {
                    "channel": channel_id,
                    "oldest": str(start_ts),
                    "latest": str(end_ts),
                    "limit": 200
                }
                if cursor:
                    params["cursor"] = cursor
                
                result = app.client.conversations_history(**params)
                batch_messages = result.get("messages", [])
                messages.extend(batch_messages)
                
                logger.info(f"Page {page}: Retrieved {len(batch_messages)} messages")
                
                cursor = result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
                    
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching page {page}: {str(e)}")
                break
        
        # Sort messages by timestamp (oldest first)
        messages.sort(key=lambda x: float(x.get('ts', 0)))
        
        logger.info(f"Total retrieved {len(messages)} messages from {channel_name} between {start_date} and {end_date}")
        return messages, None
        
    except Exception as e:
        logger.error(f"Error extracting channel history: {str(e)}", exc_info=True)
        return None, str(e)

# Channel Extraction
def handle_channel_extraction(event, client):
    """Handle channel extraction command"""
    try:
        user_id = event.get("user")
        text = event.get("text", "").strip()
        channel_id = event.get("channel")
        event_ts = event.get("ts")

        if not user_id:
            logger.error("No user_id found in event.")
            return

        # Remove bot mention from text
        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        # Parse channel extraction pattern
        pattern = r"extract from\s+#?([\w\-]+)\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})"
        match = re.search(pattern, text, re.IGNORECASE)
        
        if not match:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text="No messages found in"
            )
            return

        channel_name, start_date_str, end_date_str = match.groups()

        # Validate dates
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
            if start_date > end_date:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text="Start date cannot be after end date."
                )
                return
        except ValueError:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text="Invalid date format. Use YYYY-MM-DD."
            )
            return

        logger.info(f"Extracting from channel: {channel_name} from {start_date_str} to {end_date_str}")

        # Send initial response in thread
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Extracting messages from #{channel_name} between {start_date_str} and {end_date_str}..."
        )

        # Extract channel messages
        messages, error = extract_channel_history(channel_name, start_date_str, end_date_str)
        
        if error:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text=f"Error: {error}"
            )
            return

        if not messages:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text=f"No messages found in #{channel_name} between {start_date_str} and {end_date_str}."
            )
            return

        # Build user map and conversation text
        user_map = {}
        conversation_text = ""
        
        for msg in messages:
            msg_user = msg.get("user")
            msg_text = msg.get("text", "").strip()
            
            if not msg_text or msg.get("bot_id") or "extract from" in msg_text.lower():
                continue
                
            # Get or cache user info
            if msg_user and msg_user not in user_map:
                try:
                    user_info = client.users_info(user=msg_user)
                    user_name = user_info["user"]["profile"].get("real_name") or user_info["user"]["profile"].get("display_name") or msg_user
                    user_map[msg_user] = user_name
                except Exception as e:
                    user_map[msg_user] = msg_user
                    logger.warning(f"Could not get user info for {msg_user}: {e}")
            
            user_name = user_map.get(msg_user, msg_user)
            conversation_text += f"{user_name}: {msg_text}\n"

        if not conversation_text.strip():
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text=f"No meaningful conversation found in #{channel_name} for the specified period."
            )
            return

        # Generate action items
        logger.info("Generating action items from channel conversation...")
        action_items_text = action_item_generator.generate(conversation_text, context_type="channel")

        # Parse action items
        action_items = []
        if action_items_text and action_items_text != "No actionable tasks detected.":
            for line in action_items_text.split('\n'):
                line = line.strip()
                if line.startswith('* - [') and ']:' in line:
                    try:
                        user_part, task_part = line.split(']:', 1)
                        user = user_part.replace('* - [', '').strip()
                        task = task_part.strip()
                        
                        # Try to find user in conversation
                        found_user = user
                        for known_user in user_map.values():
                            if known_user.lower() in task.lower() or known_user.lower() in user.lower():
                                found_user = known_user
                                break
                        
                        if task:
                            action_items.append({
                                "action": task,
                                "responsible": found_user,
                                "deadline": ""
                            })
                    except ValueError:
                        continue

        if not action_items:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text="No actionable tasks found in the channel conversation."
            )
            return

        # Save tasks to DB
        saved_count = 0
        for item in action_items:
            desc = item.get("action")
            responsible = item.get("responsible", "")
            if desc:
                save_task_to_db(
                    user_id=user_id,
                    user_name=responsible,
                    task_description=desc,
                    deadline="",
                    channel_id=channel_id,
                    message_ts=event_ts,
                    original_thread_ts=event_ts
                )
                saved_count += 1

        # Post action items
        post_action_items_with_checkboxes(
            app=app,
            action_items=action_items,
            channel_id=channel_id,
            thread_ts=event_ts,
            source_channel=channel_name
        )

        logger.info(f"Extracted {len(action_items)} tasks from channel #{channel_name}")

    except SlackApiError as e:
        logger.error(f"Slack API error: {e.response['error']}")
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Slack API error: {e.response['error']}"
        )
    except Exception as e:
        logger.error(f"Error in handle_channel_extraction: {str(e)}", exc_info=True)
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Error extracting channel tasks: {str(e)}"
        )

# Thread Extraction
def handle_thread_extraction(event, client):
    """Handle thread extraction"""
    try:
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        text = event.get("text", "").strip()

        if not thread_ts:
            logger.error("No thread_ts found for thread extraction")
            return

        # Remove bot mention from text
        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        logger.info(f"Extracting from thread {thread_ts} in channel {channel_id}")

        # Send initial response
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Extracting tasks from this thread..."
        )

        # Fetch thread replies
        try:
            result = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=1000
            )
            messages = result.get("messages", [])
        except SlackApiError as e:
            logger.error(f"Error fetching thread replies: {e.response['error']}")
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Error fetching thread messages: {e.response['error']}"
            )
            return

        if len(messages) <= 1:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="No meaningful conversation found in this thread."
            )
            return

        # Build conversation text
        user_map = {}
        conversation_text = ""
        for msg in messages:
            msg_user = msg.get("user", "unknown")
            msg_text = msg.get("text", "").strip()
            if msg_text and not msg.get("bot_id"):
                if "extract" in msg_text.lower() and f"<@{BOT_USER_ID}>" in msg_text:
                    continue
                    
                # Get user info
                if msg_user not in user_map:
                    try:
                        user_info = client.users_info(user=msg_user)
                        user_name = user_info["user"]["profile"].get("real_name") or user_info["user"]["profile"].get("display_name") or msg_user
                        user_map[msg_user] = user_name
                    except:
                        user_map[msg_user] = msg_user
                user_name = user_map[msg_user]
                conversation_text += f"{user_name}: {msg_text}\n"

        if not conversation_text.strip():
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="No meaningful conversation found in this thread."
            )
            return

        # Generate action items
        logger.info("Generating action items from thread conversation...")
        action_items_text = action_item_generator.generate(conversation_text, context_type="thread")

        # Parse action items
        action_items = []
        if action_items_text and action_items_text != "No actionable tasks detected.":
            for line in action_items_text.split('\n'):
                line = line.strip()
                if line.startswith('* - [') and ']:' in line:
                    try:
                        user_part, task_part = line.split(']:', 1)
                        user = user_part.replace('* - [', '').strip()
                        task = task_part.strip()
                        
                        # Try to find user in conversation
                        found_user = user
                        for known_user in user_map.values():
                            if known_user.lower() in task.lower() or known_user.lower() in user.lower():
                                found_user = known_user
                                break
                        
                        if task:
                            action_items.append({
                                "action": task,
                                "responsible": found_user,
                                "deadline": ""
                            })
                    except ValueError:
                        continue

        if not action_items:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="No actionable tasks found in this thread."
            )
            return

        # Save tasks to DB
        saved_count = 0
        for item in action_items:
            desc = item.get("action")
            responsible = item.get("responsible", "")
            if desc:
                save_task_to_db(
                    user_id=user_id,
                    user_name=responsible,
                    task_description=desc,
                    deadline="",
                    channel_id=channel_id,
                    message_ts=thread_ts,
                    original_thread_ts=thread_ts
                )
                saved_count += 1

        # Post action items
        post_action_items_with_checkboxes(
            app=app,
            action_items=action_items,
            channel_id=channel_id,
            thread_ts=thread_ts,
            source_channel="this thread"
        )

        logger.info(f"Extracted {saved_count} tasks from thread {thread_ts}")

    except Exception as e:
        logger.error(f"Error in handle_thread_extraction: {str(e)}", exc_info=True)
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"Error extracting thread tasks: {str(e)}"
        )

# DM Extraction
def handle_dm_extraction(event, client):
    """Handle DM extraction between two users"""
    try:
        user_id = event.get("user")
        text = event.get("text", "").strip()
        channel_id = event.get("channel")
        event_ts = event.get("ts")

        if not user_id:
            logger.error("No user_id found in event.")
            return

        # Remove bot mention from text
        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        # Parse DM extraction pattern - updated to be more flexible
        # Supports both "extract dm between user1 user2" and "extract dm user1 user2"
        pattern = r"extract dm (?:between\s+)?(\w+)\s+(\w+)(?:\s+from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2}))?"
        match = re.search(pattern, text, re.IGNORECASE)
        
        if match:
            user1_name, user2_name, start_date_str, end_date_str = match.groups()
            
            # Set default date range if not provided
            if not start_date_str or not end_date_str:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                start_date_str = start_date.strftime("%Y-%m-%d")
                end_date_str = end_date.strftime("%Y-%m-%d")
            else:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

            logger.info(f"Extracting DM between {user1_name} and {user2_name} from {start_date_str} to {end_date_str}")

            # Send initial response
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text=f"Extracting DM messages between {user1_name} and {user2_name} from {start_date_str} to {end_date_str}..."
            )

            # Get user IDs
            user1_id, user1_full_name = get_user_id_by_name_part(user1_name)
            user2_id, user2_full_name = get_user_id_by_name_part(user2_name)

            if not user1_id or not user2_id:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"Could not find one or both users. Found: {user1_full_name or 'None'}, {user2_full_name or 'None'}"
                )
                return

            # Fetch DM messages
            messages = fetch_dm_messages_between_users(user1_name, user2_name)
            
            if not messages:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"No DM channel found between {user1_full_name} and {user2_full_name}"
                )
                return

            # Filter messages by date range
            filtered_messages = [
                msg for msg in messages
                if start_date <= datetime.fromtimestamp(float(msg["ts"])) <= end_date
            ]

            if not filtered_messages:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"No messages found in DMs between {user1_full_name} and {user2_full_name} between {start_date_str} and {end_date_str}."
                )
                return

            # Build user map for LLM
            user_map = {
                user1_id: user1_full_name or user1_name,
                user2_id: user2_full_name or user2_name
            }

            # Use LLM to extract action items
            action_items = extract_action_items_llm(filtered_messages, user_map)

            if not action_items or (len(action_items) == 1 and "error" in action_items[0]):
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text="No actionable tasks found in the DMs or there was an error processing them."
                )
                return

            # Save tasks to DB
            saved_count = 0
            for item in action_items:
                desc = item.get("action") or item.get("task") or item.get("description")
                responsible = item.get("responsible", "")
                deadline = item.get("deadline", "")
                
                if desc:
                    save_task_to_db(
                        user_id=user_id,
                        user_name=responsible,
                        task_description=desc,
                        deadline=deadline,
                        channel_id=f"dm_{user1_id}_{user2_id}",
                        message_ts=None,
                        original_thread_ts=None
                    )
                    saved_count += 1

            # Post tasks in user's DM with bot
            im_list = client.conversations_list(types="im")
            user_dm_channel = None
            for im in im_list["channels"]:
                if im["user"] == user_id:
                    user_dm_channel = im["id"]
                    break

            if user_dm_channel:
                post_action_items_with_checkboxes_dm(
                    client=client,
                    channel=user_dm_channel,
                    tasks=action_items,
                    thread_ts=None,
                    context=f"DM between {user1_full_name} and {user2_full_name}"
                )

                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"Extracted {len(action_items)} tasks from DM between {user1_full_name} and {user2_full_name}. Check your DM for the task list."
                )
            else:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"Extracted {len(action_items)} tasks but could not send them to your DM. Please check if you have a DM channel with the bot."
                )

            logger.info(f"Extracted {len(action_items)} tasks from DM between {user1_name} and {user2_name}")

        else:
            # If not DM pattern, show help
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text="Please use: @Todo Assistant extract dm user1 user2 from YYYY-MM-DD to YYYY-MM-DD\nExample: @Todo Assistant extract dm sanjay hari from 2025-10-01 to 2025-10-30"
            )

    except SlackApiError as e:
        logger.error(f"Slack API error: {e.response['error']}")
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Slack API error: {e.response['error']}"
        )
    except Exception as e:
        logger.error(f"Error in handle_dm_extraction: {str(e)}", exc_info=True)
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Error extracting DM tasks: {str(e)}"
        )


# haneesh: already available put clear condition for todo flow



# Main app mention handler
@app.event("app_mention")
def handle_app_mention(event, say, client):
    """
    Handle app mentions for channel, thread, and DM extraction
    """
    text = event.get("text", "")
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts")
    event_ts = event.get("ts")
    
    # Check for DM extraction
    if "extract dm between" in text.lower():
        handle_dm_extraction(event, client)
    # Check for channel extraction
    elif "extract from" in text and "from" in text and "to" in text:
        handle_channel_extraction(event, client)
    # Check for thread extraction
    elif thread_ts:
        handle_thread_extraction(event, client)
    else:
        # Default response with all options
        response_ts = thread_ts or event_ts
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=response_ts,
            text="I can help you extract tasks from:\n\n"
                "• *Channels*: `@Todo Assistant extract from channel_name from YYYY-MM-DD to YYYY-MM-DD`\n"
                "• *Threads*: Mention me in any thread\n"
                "• *DMs*: `@Todo Assistant extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD`"
        )

# Keep all your existing functions below (show_user_tasks, post_action_items_with_checkboxes, action handlers, etc.)
def post_action_items_with_checkboxes(app, action_items, channel_id, thread_ts, source_channel=None):
    """Post action items with interactive checkboxes in thread"""
    try:
        header_text = f"Task List ({len(action_items)})"
        if source_channel:
            header_text += f" from #{source_channel}"
        
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Click checkbox to claim your task (only authorized person can claim)"}
            },
            {"type": "divider"}
        ]
        
        for i, item in enumerate(action_items, 1):
            responsible = item.get("responsible", "Unknown")
            task_description = item.get("action", "")
            deadline = item.get("deadline") or extract_deadline_from_text(task_description) or "No Deadline"

            display_text = f"{i}) {responsible}: {task_description}"
            if deadline and deadline != "No Deadline":
                display_text += f" (Due: {deadline})"

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": display_text},
                "accessory": {
                    "type": "checkboxes",
                    "action_id": f"task_checkbox_{i}",
                    "options": [
                        {
                            "text": {"type": "mrkdwn", "text": "Claim Task"},
                            "value": f"{responsible}|{task_description}|{deadline}"
                        }
                    ]
                }
            })
            blocks.append({"type": "divider"})
        
        result = app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=blocks,
            text=f"Task List ({len(action_items)} tasks)"
        )
        logger.info(f"Posted action items with checkboxes in thread: {result['ts']}")
        
    except Exception as e:
        logger.error(f"Error posting action items: {str(e)}", exc_info=True)
        raise

# ... [Keep all your existing action handlers, command handlers, etc.]
@app.action("select_tasks_to_delete")
def handle_select_tasks_to_delete(ack, body, logger):
    ack()
    logger.info("Checkbox interaction received")

@app.action("delete_selected_tasks")
def handle_delete_selected_tasks(ack, body, client, say):
    ack()
    user_id = body["user"]["id"]

    # Find selected checkboxes
    selected_tasks = []
    for block in body["state"]["values"].values():
        for action in block.values():
            if action["type"] == "checkboxes":
                selected_tasks = [opt["value"] for opt in action.get("selected_options", [])]

    if not selected_tasks:
        say(text="Please select at least one task to delete.", thread_ts=body["message"]["ts"])
        return

    # Delete tasks
    deleted_count = 0
    for task_id in selected_tasks:
        deleted = delete_task(task_id)
        if deleted:
            deleted_count += 1

    say(
        text=f"Deleted {deleted_count} task(s).",
        thread_ts=body["message"]["ts"]
    )

    # Refresh updated task list
    show_user_tasks(user_id, body["channel"]["id"], body["message"]["ts"], say)



# haneesh:- handler already there need to put condition to redirect to todo flow, normal flow should work fine as a regular chat bot
@app.event("message")
def handle_message_events(body, say, logger):
    """Handle direct channel messages"""
    try:
        event = body.get("event", {})
        text = event.get("text", "").lower()
        user_id = event.get("user")
        channel_id = event.get("channel")
        
        # Skip bot messages, thread messages, and bot mentions
        if event.get("bot_id") or event.get("thread_ts") or (BOT_USER_ID and f"<@{BOT_USER_ID}>" in text):
            return
        
        # Check for task-related commands
        if "show my tasks" in text or "my tasks" in text or "show task" in text or "show tasks" in text:
            show_user_tasks(user_id, channel_id, None, say)
            return
            
    except Exception as e:
        logger.error(f"Error in message handler: {str(e)}", exc_info=True)

@app.action(re.compile("task_checkbox_.*"))
def handle_task_checkbox(ack, body, action):
    """Handle checkbox selection for tasks - save to DB only if user matches"""
    ack()
    try:
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        # Get logged-in user info
        user_info = app.client.users_info(user=user_id)
        user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or user_info["user"]["name"]

        # Decode structured value
        selected = action.get("selected_options", [])
        if not selected:
            return

        value = selected[0]["value"]
        assigned_user, task_description, deadline = value.split("|")

        # Check if logged-in user matches the assigned user
        if assigned_user.lower() != user_name.lower():
            app.client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Only {assigned_user} can claim this task."
            )
            return
        
        # Check for existing task (prevent duplicates)
        if check_existing_task(user_id, task_description):
            app.client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"You already claimed this task: {task_description}"
            )
            logger.info(f"Duplicate task prevented for user={user_name}: {task_description}")
            return
        
        # Save into DB - only if user matches
        task_id = save_task_to_db(
            user_id=user_id,
            user_name=user_name,
            task_description=task_description,
            deadline=None if deadline == "No Deadline" else deadline,
            channel_id=channel_id,
            message_ts=message_ts,
            original_thread_ts=None
        )

        if task_id:
            app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"Task claimed by {user_name}\nTask: {task_description}\nDeadline: {deadline}\nTask ID: {task_id}"
            )
            logger.info(f"Task claimed successfully: ID={task_id}, User={user_name}")

    except Exception as e:
        logger.error(f"Error handling checkbox: {str(e)}", exc_info=True)
        app.client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"Error claiming task. Please try again."
        )


# haneesh:- not needed aready there
@app.event("app_home_opened")
def handle_app_home_opened_events(body, logger):
    logger.info(body)


@app.action("claim_task_action")
def handle_claim_task_action(ack, body, client, logger):
    """
    Triggered when user clicks a 'Claim Task' checkbox.
    Saves the claimed task to DB and notifies user.
    """
    ack()  # Must always ACK first to avoid timeout

    try:
        user_id = body["user"]["id"]
        user_info = client.users_info(user=user_id)
        user_name = user_info["user"]["profile"]["real_name"]

        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        original_thread_ts = body.get("container", {}).get("thread_ts", None)

        selected = body["actions"][0].get("selected_options", [])
        if not selected:
            return

        # Get the value directly instead of parsing as JSON
        value = selected[0]["value"]
        
        # Parse the pipe-separated values (same format as used in post_action_items_with_checkboxes)
        if "|" in value:
            parts = value.split("|")
            if len(parts) >= 3:
                responsible = parts[0]
                task_description = parts[1]
                deadline = parts[2] if len(parts) > 2 else ""
            else:
                # Fallback if format doesn't match expected
                responsible = user_name
                task_description = value
                deadline = ""
        else:
            # If it's not pipe-separated, use the whole value as task description
            responsible = user_name
            task_description = value
            deadline = ""

        # Save task into DB
        task_id = save_task_to_db(
            user_id=user_id,
            user_name=user_name,
            task_description=task_description,
            deadline=deadline if deadline and deadline != "No Deadline" else None,
            channel_id=channel_id,
            message_ts=message_ts,
            original_thread_ts=original_thread_ts,
        )

        if task_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Task '{task_description}' saved to DB by {user_name} (Task ID: {task_id})"
            )
        else:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Failed to save task '{task_description}'."
            )

    except Exception as e:
        logger.error(f"Error handling task claim: {e}", exc_info=True)
        client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text=f"Error saving task: {str(e)}"
        )

@app.command("/extract_all_tasks")
def extract_all_tasks_command(ack, command, respond):
    """
    Slash command to extract tasks from all DMs.
    Usage: /extract_all_tasks 2025-01-01 2025-01-31
    """
    ack()
    
    try:
        # Parse dates from command text
        parts = command["text"].strip().split()
        if len(parts) != 2:
            respond("Usage: /extract_all_tasks YYYY-MM-DD YYYY-MM-DD")
            return
        
        start_date, end_date = parts
        
        # Validate dates
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            if start_dt > end_dt:
                respond("Start date cannot be after end date.")
                return
        except ValueError:
            respond("Invalid date format. Use YYYY-MM-DD.")
            return
        
        respond(f"Extracting tasks from all DMs ({start_date} to {end_date})... This may take a moment...")
        
        # Since we removed the old DM extraction, inform user about new method
        respond("DM extraction is now available via: @Todo Assistant extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD")
        
    except Exception as e:
        logger.error(f"Error in extract_all_tasks_command: {e}", exc_info=True)
        respond(f"Error: {e}")

@app.command("/show_tasks")
def show_tasks_command(ack, command, respond):
    """Slash command to show user's tasks"""
    ack()
    user_id = command["user_id"]
    channel_id = command["channel_id"]
    
    try:
        show_user_tasks(user_id, channel_id, None)
        respond("Displaying your tasks...")
    except Exception as e:
        logger.error(f"Error in show_tasks_command: {str(e)}", exc_info=True)
        respond("Error loading tasks. Please try again.")

@app.command("/help")
def help_command(ack, command, respond):
    """Slash command to show help"""
    ack()
    
    help_text = """
Todo Assistant Help

Available Commands:
/show_tasks - Show your pending tasks
/help - Show this help message

Channel Extraction:
Mention me with: @Todo Assistant extract from channel_name from YYYY-MM-DD to YYYY-MM-DD
Example: @Todo Assistant extract from general from 2025-10-01 to 2025-10-31

Thread Extraction:
Mention me in any thread to extract tasks from that conversation

DM Extraction:
Mention me with: @Todo Assistant extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD
Example: @Todo Assistant extract dm between sanjay hari from 2025-10-01 to 2025-10-31

Task Management:
Say "show my tasks" in any channel to see your tasks
Click checkboxes to claim tasks
Use the delete button to remove completed tasks

Need more help? Contact the administrator.
"""
    
    respond(help_text)

@app.command("/extract_dm")
def extract_dm_command(ack, command, respond):
    """Slash command for DM extraction"""
    ack()
    
    help_text = """
DM Extraction Usage:

Via mention:
@Todo Assistant extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD

Examples:
@Todo Assistant extract dm between sanjay hari
@Todo Assistant extract dm between alice bob from 2025-10-01 to 2025-10-31

Note: If no dates are provided, last 30 days will be used.
"""
    
    respond(help_text)

@app.command("/extract_channel")
def extract_channel_command(ack, command, respond):
    """Slash command for channel extraction"""
    ack()
    
    help_text = """
Channel Extraction Usage:

Via mention:
@Todo Assistant extract from channel_name from YYYY-MM-DD to YYYY-MM-DD

Examples:
@Todo Assistant extract from general from 2025-10-01 to 2025-10-31
@Todo Assistant extract from project-updates from 2025-09-01 to 2025-09-30
"""
    
    respond(help_text)

# Error handler
@app.error
def global_error_handler(error, body, logger):
    logger.error(f"Error: {error}")
    logger.error(f"Request body: {body}")

def show_user_tasks(user_id, channel_id, thread_ts, say=None):
    """Show all pending tasks for user in an interactive list with checkboxes to delete selected tasks"""
    try:
        user_info = app.client.users_info(user=user_id)
        user_name = (
            user_info["user"]["profile"].get("display_name")
            or user_info["user"]["profile"].get("real_name")
            or user_info["user"]["name"]
        )

        tasks = get_user_tasks(user_id, status="pending")

        if not tasks:
            message = f"No pending tasks for {user_name}!"
            if say:
                say(text=message, thread_ts=thread_ts)
            else:
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=message
                )
            return

        # Limit tasks to 9 or fewer to avoid Slack's 10-item limit
        if len(tasks) > 9:
            tasks = tasks[:9]
            task_count_text = f" (showing 9 of {len(tasks)} total tasks)"
        else:
            task_count_text = f" ({len(tasks)})"

        # Build checkbox options (limited to 9 items)
        checkbox_options = []
        for task_id, description, deadline, status, created_at in tasks:
            deadline_text = deadline if deadline else "No Deadline"
            # Truncate long task descriptions to avoid overflow
            if len(description) > 100:
                description = description[:97] + "..."
            label = f"{description} (Deadline: {deadline_text})"
            checkbox_options.append({
                "text": {
                    "type": "plain_text",
                    "text": label,
                    "emoji": True
                },
                "value": str(task_id)
            })

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{user_name}'s Pending Tasks{task_count_text}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Select tasks below and click Delete Selected to remove them."
                }
            },
            {
                "type": "actions",
                "block_id": "task_selection_block",
                "elements": [
                    {
                        "type": "checkboxes",
                        "action_id": "select_tasks_to_delete",
                        "options": checkbox_options
                    }
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Delete Selected"},
                        "style": "danger",
                        "action_id": "delete_selected_tasks"
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Tip: Mention me in a thread to extract more tasks!"
                    }
                ]
            }
        ]

        if say:
            say(blocks=blocks, thread_ts=thread_ts)
        else:
            app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=blocks,
                text=f"{user_name}'s Pending Tasks"
            )

    except Exception as e:
        logger.error(f"Error showing tasks: {str(e)}", exc_info=True)
        error_message = "Error loading tasks. Please try again."
        if say:
            say(text=error_message, thread_ts=thread_ts)
        else:
            app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=error_message
            )



# haneesh :  already there
if __name__ == "__main__":
    logger.info("Starting Slack bot with channel, thread, and DM extraction features...")
    try:
        handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
        handler.start()
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}", exc_info=True)