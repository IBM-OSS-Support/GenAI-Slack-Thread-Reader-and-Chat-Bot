from datetime import datetime
import logging
import re
import pytz
from slack_sdk.errors import SlackApiError
from action_item_generator import ActionItemGenerator
from http.client import IncompleteRead
import time


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("slack_bot.log", encoding='utf-8'),
        logging.StreamHandler(),
    ]
)

logger = logging.getLogger(__name__)

seoul_tz = pytz.timezone('Asia/Seoul')

import re
from datetime import datetime, timedelta

def extract_deadline_from_text(text):
    """Extract and convert phrases like 'by today', 'by next Friday', or 'by Nov 10' into date format."""
    text = text.lower().strip()
    today = datetime.now().date()

    # Common patterns
    patterns = [
        r"\bby (today|tomorrow)\b",
        r"\bby next (\w+day)\b",
        r"\bby (\w+day)\b",
        r"\bby (\d{4}-\d{2}-\d{2})\b",
        r"\bby ([a-zA-Z]+\s\d{1,2}(?:st|nd|rd|th)?)\b"
    ]

    # Match loop
    for p in patterns:
        m = re.search(p, text)
        if not m:
            continue
        val = m.group(1).strip()

        # --- Relative days ---
        if val == "today":
            return today.strftime("%Y-%m-%d")
        elif val == "tomorrow":
            return (today + timedelta(days=1)).strftime("%Y-%m-%d")

        # --- Weekday detection ---
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        if val in weekdays or val.startswith("next "):
            target = val.replace("next ", "")
            if target in weekdays:
                current_idx = today.weekday()
                target_idx = weekdays.index(target)
                days_ahead = (target_idx - current_idx + 7) % 7
                if "next" in val or days_ahead == 0:
                    days_ahead += 7
                return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # --- YYYY-MM-DD format ---
        try:
            return datetime.strptime(val, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass

        # --- Month and day (e.g. Nov 10th) ---
        try:
            clean_val = re.sub(r"(st|nd|rd|th)", "", val)
            parsed_date = datetime.strptime(clean_val.strip(), "%b %d")
            parsed_date = parsed_date.replace(year=today.year)
            return parsed_date.strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


# Example test inputs
tasks = [
    "Sanjay Srivastava: Test OpenShift deployment by today",
    "Hari: Troubleshoot JVM issue in Apache Sea Tunnel",
    "Amit: Review API code by next Monday",
    "Neha: Prepare release notes by Friday",
    "Priya: Update dashboard by Nov 10th",
    "Karan: Fix build pipeline by 2025-11-20"
]

# Print formatted output
for task in tasks:
    due_date = extract_deadline_from_text(task)
    if not due_date:
        due_date = "no deadline"
    print(f"{task} (Due: {due_date})")


def get_all_dm_channels(app):
    """
    Retrieve all DM (IM) channels where the bot is present.
    Returns a list of (channel_id, user_id) tuples.
    """
    try:
        result = app.client.conversations_list(
            types="im",
            exclude_archived=True,
            limit=1000
        )
        
        dm_channels = []
        for channel in result.get("channels", []):
            channel_id = channel.get("id")
            user_id = channel.get("user")
            if channel_id and user_id:
                dm_channels.append((channel_id, user_id))
        
        logger.info(f"Found {len(dm_channels)} DM channels")
        return dm_channels
    
    except SlackApiError as e:
        logger.error(f"Error fetching DM channels: {e}", exc_info=True)
        return []

def check_bot_access_to_dm(app, user_id):
    """
    Check if the bot has access to a user's DM.
    Returns (has_access: bool, dm_channel_id: str|None)
    """
    try:
        dm_resp = app.client.conversations_open(users=[user_id])
        dm_channel_id = dm_resp["channel"]["id"]
        return True, dm_channel_id
    except SlackApiError as e:
        if e.response.get("error") in ["not_in_channel", "channel_not_found"]:
            return False, None
        logger.error(f"Error checking DM access for {user_id}: {e}")
        return False, None


def extract_tasks_from_all_dms(app, start_date, end_date):
    """
    Extract tasks from all DMs where bot has access.
    Returns: (all_user_tasks, skipped_users)
    """
    try:
        # Get all users
        result = app.client.users_list()
        users = result.get("members", [])
        
        all_user_tasks = {}
        skipped_users = []
        
        for user in users:
            user_id = user.get("id")
            is_bot = user.get("is_bot", False)
            deleted = user.get("deleted", False)
            
            # Skip bots and deleted users
            if is_bot or deleted:
                continue
            
            # Check if bot has access
            has_access, dm_channel = check_bot_access_to_dm(app, user_id)
            
            if not has_access:
                skipped_users.append(user_id)
                logger.info(f"Skipping user {user_id} - bot not in DM")
                continue
            
            try:
                # Extract tasks (assuming this function exists in your code)
                tasks = extract_from_dm_conversation(app, user_id, start_date, end_date)
                
                if tasks:
                    all_user_tasks[user_id] = tasks
                    logger.info(f"Extracted {len(tasks)} tasks from user {user_id}")
            
            except Exception as e:
                logger.error(f"Error extracting tasks from user {user_id}: {e}")
                continue
        
        return all_user_tasks, skipped_users
    
    except Exception as e:
        logger.error(f"Error in extract_tasks_from_all_dms: {e}", exc_info=True)
        return {}, []

def find_or_open_dm_channel(app, user_id):
    """
    Find a DM channel between bot and user. If not found, open a new DM.
    Returns channel_id or None if failed.
    """
    try:
        # List bot accessible DMs
        result = app.client.conversations_list(types="im", limit=1000)
        for dm in result.get("channels", []):
            if dm.get("user") == user_id:
                return dm["id"]

        # If not found, open a new DM
        open_resp = app.client.conversations_open(users=user_id)
        if open_resp.get("ok"):
            return open_resp["channel"]["id"]
        else:
            return None
    except Exception as e:
        logger.error(f"Error finding/opening DM channel for user {user_id}: {str(e)}")
        return None


def safe_slack_api_call(app, method, **params):
    """
    Safely executes Slack API calls with retry logic for IncompleteRead errors.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client_method = getattr(app.client, method)
            return client_method(**params)
        except IncompleteRead as e:
            logger.warning(f"IncompleteRead during {method}: attempt {attempt+1}/{max_retries}")
            time.sleep(1.5)
            if attempt == max_retries - 1:
                raise
        except SlackApiError as e:
            logger.error(f"Slack API error calling {method}: {e.response.get('error')}")
            break
        except Exception as e:
            logger.error(f"Unexpected error calling {method}: {e}")
            time.sleep(1)
    return {"messages": []}


def open_dm_channel(app, user_id):
    """
    Safely open a DM channel with a user. Returns channel_id or None on failure.
    """
    try:
        # Check existing IMs
        result = app.client.conversations_list(types="im", limit=1000)
        for dm in result.get("channels", []):
            if dm.get("user") == user_id:
                return dm["id"]

        # Open new DM
        open_resp = app.client.conversations_open(users=user_id)
        if open_resp.get("ok"):
            return open_resp["channel"]["id"]
    except Exception as e:
        logger.error(f"Error opening DM for user {user_id}: {e}")
    return None

def extract_from_dm_conversation(app, user_id, start_date, end_date):
    """
    Extract actionable tasks from the requesting user's DM conversation
    between start_date and end_date.
    Returns a list of formatted action items for Slack.
    """
    dm_channel_id = find_or_open_dm_channel(app, user_id)
    if not dm_channel_id:
        dm_resp = app.client.conversations_open(users=[user_id])
        dm_channel_id = dm_resp["channel"]["id"]
        logger.error(f"Cannot find or open DM channel for {user_id}")
        return []

    try:
        # Convert dates to timestamps
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=seoul_tz, hour=0, minute=0, second=0
        )
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            tzinfo=seoul_tz, hour=23, minute=59, second=59
        )
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()

        # Fetch messages in DM
        # Fetch messages in DM
        messages = []
        cursor = None
        while True:
            params = {
                "channel": dm_channel_id,
                "oldest": str(start_ts),
                "latest": str(end_ts),
                "limit": 100,  # use smaller page size for better reliability
            }
            if cursor:
                params["cursor"] = cursor

            # Use safe API call instead of direct Slack SDK call
            resp = safe_slack_api_call(app, "conversations_history", **params)

            if not resp or "messages" not in resp:
                logger.warning(f"No messages key received for {user_id}")
                break

            messages.extend(resp.get("messages", []))
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break


        if not messages:
            app.client.chat_postMessage(
                channel=dm_channel_id,
                text=f"✅ No messages found in DM between {start_date} and {end_date}.",
            )
            return []

        # Filter only messages from the requesting user
        user_messages = [m for m in messages if m.get("user") == user_id]
        if not user_messages:
            app.client.chat_postMessage(
                channel=dm_channel_id,
                text=f"✅ No messages from you in DM between {start_date} and {end_date}.",
            )
            return []

        # Sort messages oldest first
        user_messages.sort(key=lambda x: float(x.get("ts", 0)))
        # Initialize ActionItemGenerator
        generator = ActionItemGenerator(slack_app=app)

        # Prepare readable conversation text
        prepared_text = generator._prepare_conversation(user_messages, context_type="dm")

        # Generate action items (pass messages list to LLM generator)
        action_items = generator.generate(prepared_text, context_type="dm")

        if isinstance(action_items, str):
            action_items = [line.strip() for line in action_items.splitlines() if line.strip()]

        return action_items
    except Exception as e:
        logger.error(f"Error extracting DM conversation for user {user_id}: {str(e)}", exc_info=True)
        try:
            app.client.chat_postMessage(
                channel=dm_channel_id,
                text=f"⚠️ Error extracting tasks: {str(e)}"
            )
        except:
            pass
        return []