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
from helper.dm_utils import (get_user_id_by_name_part, 
                            fetch_dm_messages_between_users, 
                            post_action_items_with_checkboxes_dm,summarize_task_llm,parse_natural_deadline,is_duplicate_task)
from helper.llm_utils import extract_action_items_llm
from helper.utils import extract_deadline_from_text

#Multi-team token configuration
TEAM_BOT_TOKENS = {
    os.getenv("TEAM1_ID"): os.getenv("TEAM1_BOT_TOKEN"),
    os.getenv("TEAM2_ID"): os.getenv("TEAM2_BOT_TOKEN"),
}

# Get default token (fallback to TEAM2 if TEAM1 not available)
DEFAULT_TEAM_ID = os.getenv("TEAM1_ID") or os.getenv("TEAM2_ID")
SLACK_BOT_TOKEN = os.getenv("TEAM1_BOT_TOKEN") or os.getenv("TEAM2_BOT_TOKEN")
client = WebClient(token=SLACK_BOT_TOKEN)
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

MODEL_TYPE = os.getenv("MODEL_TYPE", "ollama")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

app = App(token=SLACK_BOT_TOKEN)

print("Bot token:",(SLACK_BOT_TOKEN))

# Get bot user ID dynamically
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
        pattern = r"extract from\s+#?([\w\-]+)\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})".strip()
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
        pattern = r"extract dm (?:between\s+)?(\w+)\s+(\w+)(?:\s+from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2}))?".strip()
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
            # saved_count = 0
            # for item in action_items:
            #     desc = item.get("action") or item.get("task") or item.get("description")
            #     responsible = item.get("responsible", "")
            #     deadline = item.get("deadline", "")
                
            #     if desc:
            #         save_task_to_db(
            #             user_id=user_id,
            #             user_name=responsible,
            #             task_description=desc,
            #             deadline=deadline,
            #             channel_id=f"dm_{user1_id}_{user2_id}",
            #             message_ts=None,
            #             original_thread_ts=None
            #         )
            #         saved_count += 1

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

# ... 
















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

if __name__ == "__main__":
    logger.info("Starting Slack bot with channel, thread, and DM extraction features...")
    try:
        handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
        handler.start()
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}", exc_info=True)