from dotenv import load_dotenv
from utils.progress_bar import ProgressBar
from utils.progress_card import ProgressCard
from utils.resolve_user_mentions import resolve_user_mentions
load_dotenv()
from utils.global_kb import index_startup_files, query_global_kb
from utils.product_profile import get_product_profile
import json
import os
import threading
import time
import re
import sys
import logging
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_bolt.authorization import AuthorizeResult
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import io
from utils.slack_api import send_message
from chains.chat_chain_mcp import process_message_mcp, _get_memory, _memories
from chains.analyze_thread import analyze_slack_thread
from utils.channel_rag import analyze_entire_channel
from utils.slack_tools import get_user_name
from utils.export_pdf import render_summary_to_pdf
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from utils.file_utils import download_slack_file, extract_text_from_file, extract_excel_as_table, dataframe_to_documents, answer_from_excel_super_dynamic
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.vector_store import FaissVectorStore
from utils.vector_store import FaissVectorStore
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.thread_store import THREAD_VECTOR_STORES, EXCEL_TABLES
from chains.analyze_thread import translation_chain
from utils.health import health_app, run_health_server
from utils.innovation_report import parse_innovation_sheet
from utils.usage_guide import get_usage_guide
from chains.analyze_thread import analyze_slack_thread, custom_chain  # Removed THREAD_ANALYSIS_BLOBS


# ─────────────────────────────────────────────────────────────────────────────
# Import Action Item Bot dependencies starting here
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta
import pytz
from action_item_generator import ActionItemGenerator
from db import check_existing_task, delete_task, get_user_tasks, save_task_to_db
from helper.dm_utils import get_user_id_by_name_part, fetch_dm_messages_between_users, post_action_items_with_checkboxes_dm
from helper.llm_utils import extract_action_items_llm
from helper.utils import extract_deadline_from_text
# ─────────────────────────────────────────────────────────────────────────────
# Import Action Item Bot dependencies ending  here
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration Action Item Bot starting here
# ─────────────────────────────────────────────────────────────────────────────
class SeoulFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        return dt.astimezone(seoul_tz)
    
    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('slack_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Apply Seoul timezone formatter
seoul_tz = pytz.timezone('Asia/Seoul')
for handler in logging.getLogger().handlers:
    handler.setFormatter(SeoulFormatter())

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration Action Item Bot ending here
# ─────────────────────────────────────────────────────────────────────────────

# Instantiate a single global vector store
if not os.path.exists("data"):
    os.makedirs("data", exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Environment Variables & Configuration coming here
# ─────────────────────────────────────────────────────────────────────────────
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
BOT_USER_ID = os.getenv("BOT_USER_ID")

TEAM_BOT_TOKENS = {
    os.getenv("TEAM1_ID"): os.getenv("TEAM1_BOT_TOKEN"),
    os.getenv("TEAM2_ID"): os.getenv("TEAM2_BOT_TOKEN"),
}

formatted = os.getenv("FORMATTED_CHANNELS", "")
FORMATTED_CHANNELS = {ch.strip() for ch in formatted.split(",") if ch.strip()}
logger.info(f"Formatted channels: {FORMATTED_CHANNELS}")


# ─────────────────────────────────────────────────────────────────────────────
# Action Item Bot Configuration
MODEL_TYPE = os.getenv("MODEL_TYPE", "ollama")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
# ─────────────────────────────────────────────────────────────────────────────

# Define THREAD_ANALYSIS_BLOBS if it doesn't exist in the imported module
THREAD_ANALYSIS_BLOBS = {}

# ─────────────────────────────────────────────────────────────────────────────
# Multi‑workspace router with automatic fallback
# ─────────────────────────────────────────────────────────────────────────────
class WorkspaceRouter:
    def __init__(self, team_tokens: dict[str, str]):
        self.team_tokens = {k: v for k, v in team_tokens.items() if k and v}
        if not self.team_tokens:
            raise RuntimeError("No workspace tokens configured!")
        self.default_team_id = next(iter(self.team_tokens.keys()))
        self._clients: dict[str, WebClient] = {}

    def get_client(self, team_id: str | None) -> WebClient:
        tid = team_id or self.default_team_id
        tok = self.team_tokens.get(tid)
        if not tok:
            tid = self.default_team_id
        if tid not in self._clients:
            self._clients[tid] = WebClient(token=self.team_tokens[tid])
        return self._clients[tid]

    def iter_clients_with_priority(self, primary_team_id: str | None):
        seen = set()
        order = []
        if primary_team_id and primary_team_id in self.team_tokens:
            order.append(primary_team_id)
            seen.add(primary_team_id)
        for tid in self.team_tokens:
            if tid not in seen:
                order.append(tid)
        for tid in order:
            yield tid, self.get_client(tid)

    def find_channel_anywhere(self, raw: str) -> tuple[str, str] | None:
        if raw.startswith("C") and raw.isupper():
            for tid, client in self.iter_clients_with_priority(None):
                try:
                    client.conversations_info(channel=raw)
                    return tid, raw
                except SlackApiError:
                    continue
            return None

        for tid, client in self.iter_clients_with_priority(None):
            try:
                cursor = None
                while True:
                    resp = client.conversations_list(
                        types="public_channel,private_channel",
                        limit=1000,
                        cursor=cursor
                    )
                    for c in resp.get("channels", []):
                        if c.get("name") == raw:
                            return tid, c["id"]
                    cursor = resp.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
            except SlackApiError:
                continue
        return None

    def try_call(self, primary_team_id: str | None, func, *args, **kwargs):
        last_exc = None
        for tid, client in self.iter_clients_with_priority(primary_team_id):
            try:
                return tid, func(client, *args, **kwargs)
            except SlackApiError as e:
                last_exc = e
            except Exception as e:
                last_exc = e
        if last_exc:
            raise last_exc

# Global router instance
ROUTER = WorkspaceRouter(TEAM_BOT_TOKENS)

def detect_real_team_from_event(body, event) -> str | None:
    return (
        (event or {}).get("team")
        or (event or {}).get("source_team")
        or (event or {}).get("user_team")
        or (body or {}).get("team_id")
        or (body.get("authorizations") or [{}])[0].get("team_id") if body else None
    )

def get_client_for_team(team_id: str | None) -> WebClient:
    return ROUTER.get_client(team_id)

# Ensure all required env vars exist
for name in (
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "BOT_USER_ID",
    "TEAM1_ID",
    "TEAM1_BOT_TOKEN",
    "TEAM2_ID",
    "TEAM2_BOT_TOKEN",
):
    if not os.getenv(name):
        logger.error(f"⚠️ Missing env var: {name}")
        sys.exit(1)

try:
    _EXPIRATION_SECONDS = int(os.getenv("SESSION_EXPIRATION_SECONDS", "600"))
except ValueError:
    logger.warning("Invalid SESSION_EXPIRATION_SECONDS, defaulting to 600")
    _EXPIRATION_SECONDS = 600
mins = _EXPIRATION_SECONDS // 60
DEFAULT_TEAM_ID = next(iter(TEAM_BOT_TOKENS))
PLACEHOLDER_TOKEN = TEAM_BOT_TOKENS[DEFAULT_TEAM_ID]

COMMAND_KEYWORDS = {
    "analyze", "analyse", "dissect", "interpret",
    "summarize", "summarise", "recap", "review", "overview",
    "explain", "clarify", "explicate", "describe", "outline", "detail",
}

def custom_authorize(enterprise_id: str, team_id: str, logger):
    bot_token = TEAM_BOT_TOKENS.get(team_id)
    if not bot_token:
        logger.error(f"No bot token for team {team_id}")
        return None
    auth = WebClient(token=bot_token).auth_test()
    return AuthorizeResult.from_auth_test_response(
        bot_token=bot_token,
        auth_test_response=auth,
    )

app = App(
    token=PLACEHOLDER_TOKEN,
    signing_secret=SLACK_SIGNING_SECRET,
    authorize=custom_authorize,
)

# ─────────────────────────────────────────────────────────────────────────────
# Thread Analysis Bot Functions
# ─────────────────────────────────────────────────────────────────────────────
STATS_FILE = os.getenv("STATS_FILE", "/data/stats.json")

def git_md_to_slack_md(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

def index_in_background(vs, docs, client, channel_id, thread_ts, user_id, filename, real_team, ext=None):
    from utils.thread_store import EXCEL_TABLES
    client = get_client_for_team(real_team)
    try:
        vs.add_documents(docs)

        excel_info = ""
        if ext in ("xlsx", "xls") and thread_ts in EXCEL_TABLES:
            df = EXCEL_TABLES[thread_ts]
            n_rows, n_cols = df.shape
            sheet_name = getattr(df, 'sheet_name', 'Sheet1')
            excel_info = (
                f"\nSuccessfully loaded *{filename}*!\n\n"
                f":gsheet: *{sheet_name}*: {n_rows} rows, {n_cols} columns\n\n"
                f":mag: *Querying Tips:*\n"
                "• Ask about people, roles, or departments\n"
                "• Try queries like 'Who is X?', 'What is X's role?'\n"
                "• Be specific and use exact names or titles"
            )

        send_message(
            client,
            channel_id,
            f":checked: Finished indexing *{filename}*. What would you like to know?{excel_info}",
            thread_ts=thread_ts,
            user_id=user_id
        )
    except Exception as e:
        send_message(
            client,
            channel_id,
            f"❌ Failed to finish indexing *{filename}*: {e}",
            thread_ts=thread_ts,
            user_id=user_id
        )

def load_stats():
    try:
        with open(STATS_FILE) as f:
            d = json.load(f)
        return {
            "thumbs_up": d.get("thumbs_up", 0),
            "thumbs_down": d.get("thumbs_down", 0),
            "unique_users": set(range(d.get("unique_user_count", 0))),
            "total_calls": d.get("total_calls", 0),
            "analyze_calls": d.get("analyze_calls", 0),
            "analyze_followups": d.get("analyze_followups", 0),
            "general_calls": d.get("general_calls", 0),
            "general_followups": d.get("general_followups", 0),
            "pdf_exports": d.get("pdf_exports", 0),
        }
    except:
        return {
            "thumbs_up": 0,
            "thumbs_down": 0,
            "unique_users": set(),
            "total_calls": 0,
            "analyze_calls": 0,
            "analyze_followups": 0,
            "general_calls": 0,
            "general_followups": 0,
            "pdf_exports": 0,
        }

def save_stats():
    try:
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        with open(STATS_FILE, "w") as f:
            json.dump({
                "thumbs_up": _vote_up_count,
                "thumbs_down": _vote_down_count,
                "unique_user_count": len(_unique_users),
                "total_calls": USAGE_STATS["total_calls"],
                "analyze_calls": USAGE_STATS["analyze_calls"],
                "analyze_followups": USAGE_STATS["analyze_followups"],
                "general_calls": USAGE_STATS["general_calls"],
                "general_followups": USAGE_STATS["general_followups"],
                "feedback_up_reasons": _vote_reasons.get("up", []),
                "feedback_down_reasons": _vote_reasons.get("down", []),
            }, f)
    except:
        logger.exception("Failed to save stats")

_stats = load_stats()
_unique_users = _stats["unique_users"]
_vote_up_count = _stats["thumbs_up"]
_vote_down_count = _stats["thumbs_down"]
_vote_reasons = {
    "up": _stats.get("feedback_up_reasons", {}) if isinstance(_stats.get("feedback_up_reasons"), dict) else {},
    "down": _stats.get("feedback_down_reasons", {}) if isinstance(_stats.get("feedback_down_reasons"), dict) else {}
}
_feedback_submissions = set()

_last_activity = {}
_active_sessions = {}
_command_counts = {}
_vote_registry = {}
_already_warned = {}

USAGE_STATS = {
    "total_calls": _stats["total_calls"],
    "analyze_calls": _stats["analyze_calls"],
    "analyze_followups": _stats["analyze_followups"],
    "general_calls": _stats["general_calls"],
    "general_followups": _stats["general_followups"],
    "pdf_exports": _stats["pdf_exports"],
}

ANALYSIS_THREADS: set[str] = set()

def track_usage(uid, thread_ts, cmd=None):
    global _unique_users
    now = time.time()
    _active_sessions[thread_ts] = now
    _last_activity[thread_ts] = now
    before = len(_unique_users)
    _unique_users.add(uid)
    if len(_unique_users) > before:
        save_stats()
    if cmd:
        _command_counts[cmd] = _command_counts.get(cmd, 0) + 1

def get_bot_stats():
    return (
        "📊 *Bot Usage Stats*\n"
        f"• *Total calls:* {USAGE_STATS['total_calls']}\n"
        f"• *Analyze calls:* {USAGE_STATS['analyze_calls']} (follow-ups: {USAGE_STATS['analyze_followups']})\n"
        f"• *General calls:* {USAGE_STATS['general_calls']} (follow-ups: {USAGE_STATS['general_followups']})\n"
        f"• *PDF exports:* {USAGE_STATS['pdf_exports']}\n\n"
        f"👍 *{_vote_up_count}*   👎 *{_vote_down_count}*"
    )



# ─────────────────────────────────────────────────────────────────────────────
# Action Item Bot Functions starting here
# ─────────────────────────────────────────────────────────────────────────────
action_item_generator = ActionItemGenerator(
    slack_app=app, 
    model_type=MODEL_TYPE,
    model_name=OLLAMA_MODEL
)

def get_channel_id(app, channel_name):
    """Find channel ID by name"""
    try:
        result = app.client.conversations_list(types="public_channel", limit=1000)
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]
        
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
        
        messages.sort(key=lambda x: float(x.get('ts', 0)))
        
        logger.info(f"Total retrieved {len(messages)} messages from {channel_name} between {start_date} and {end_date}")
        return messages, None
        
    except Exception as e:
        logger.error(f"Error extracting channel history: {str(e)}", exc_info=True)
        return None, str(e)

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

        if len(tasks) > 9:
            tasks = tasks[:9]
            task_count_text = f" (showing 9 of {len(tasks)} total tasks)"
        else:
            task_count_text = f" ({len(tasks)})"

        checkbox_options = []
        for task_id, description, deadline, status, created_at in tasks:
            deadline_text = deadline if deadline else "No Deadline"
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





# ─────────────────────────────────────────────────────────────────────────────
# Flow Handlers - Thread Analysis Bot
# ─────────────────────────────────────────────────────────────────────────────
def handle_thread_analysis_flow(client: WebClient, event, text: str):
    """Handle thread analysis bot flow"""
    ch = event["channel"]
    ts = event["ts"]
    thread = event.get("thread_ts") or ts
    uid = event["user"]

    # Expiration check
    now = time.time()
    last = _last_activity.get(thread)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(thread, None)
        _last_activity.pop(thread, None)
        _active_sessions.pop(thread, None)
        THREAD_ANALYSIS_BLOBS.pop(thread, None)
        send_message(
            client, ch,
            f"⚠️ Conversation expired ({mins}m). Start a new one.",
            thread_ts=thread, user_id=uid
        )
        return

    # Track usage
    is_followup = (thread != ts)
    save_stats()

    # 1) Strip bot mention
    cleaned = re.sub(r"<@[^>]+>", "", text).strip()
    # 2) Unwrap URLs
    normalized = re.sub(
        r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", cleaned
    ).strip()
    normalized = normalized.replace("'","'").replace("'","'").replace("",'"').replace("",'"')
    
    # Product profile query
    m_prod = re.match(r"^-\s*(?:g\s+)?product\s+(.+)$", normalized, re.IGNORECASE)
    if m_prod:
        product_query = m_prod.group(1).strip()
        profile_text = get_product_profile(product_query, thread)
        if profile_text:
            if not is_followup:
                USAGE_STATS["general_calls"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
            save_stats()
            send_message(client, ch, profile_text, thread_ts=thread, user_id=uid)
            return
        else:
            reply = query_global_kb(f"full_product_profile::{product_query}", thread)
            if not is_followup:
                USAGE_STATS["general_calls"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
            save_stats()
            send_message(client, ch, reply, thread_ts=thread, user_id=uid)
            return
    
    # Knowledge base query
    m_kb = re.match(r"^(?:-org|-org:|-askorg|-ask:)\s*(.+)$", normalized, re.IGNORECASE)
    if m_kb:
        question = m_kb.group(1).strip()
        from chains.preanalyze import preanalyze_question
        question = preanalyze_question(question)
        reply = query_global_kb(question, thread)
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            USAGE_STATS["general_followups"] += 1
        save_stats()
        send_message(client, ch, reply, thread_ts=thread, user_id=uid)
        return

    logger.debug("🔔 Processing: %s", resolve_user_mentions(client, cleaned).strip())
    
    # Follow-up analysis - Modified to handle missing THREAD_ANALYSIS_BLOBS
    if is_followup and (thread in ANALYSIS_THREADS):
        try:
            # Try to use custom_chain if available, otherwise fallback
            if custom_chain and THREAD_ANALYSIS_BLOBS.get(thread):
                focused = custom_chain.invoke({
                    "messages": THREAD_ANALYSIS_BLOBS[thread],
                    "instructions": normalized
                }).strip()
            else:
                focused = process_message_mcp(normalized, thread)
        except Exception:
            focused = process_message_mcp(normalized, thread)

        USAGE_STATS["analyze_followups"] += 1
        save_stats()
        send_message(client, ch, focused, thread_ts=thread, user_id=uid)
        return

    # Help command
    if resolve_user_mentions(client, cleaned).strip() == "" and not event.get("files"):
        send_message(
            client,
            ch,
            ":wave: Hello! Here's how you can use me:\n"
            "- Paste a Slack thread URL along with a keyword like 'analyze', 'summarize', or 'explain' to get a formatted summary of that thread.\n"
            "- Or simply mention me and ask any question to start a chat conversation.\n"
            "- Reply inside a thread to continue the conversation with memory.",
            thread_ts=thread,
            user_id=uid,
        )
        return

    # Stats command
    if "stats" in cleaned.lower():
        send_message(
            client, ch, get_bot_stats(),
            thread_ts=thread, user_id=uid
        )
        return

    USAGE_STATS["total_calls"] += 1

    # Usage guide command
    normalized_text = resolve_user_mentions(client, cleaned).strip().lower()
    if normalized_text in ("usage", "help"):
        send_message(
            client,
            ch,
            get_usage_guide(),
            thread_ts=thread,
            user_id=uid
        )
        return

    # Channel analysis
    m_ch = re.match(
    r'^(?:analyze|analyse|summarize|summarise|explain)\s+<?#?([A-Za-z0-9_-]+)(?:\|[^>]*)?>?$',
    normalized,
    re.IGNORECASE
)
    if m_ch:
        raw = m_ch.group(1)
        found = ROUTER.find_channel_anywhere(raw)
        if not found:
            send_message(
                client, ch,
                f"❌ No channel named or ID *{raw}* found in either workspace.",
                thread_ts=thread, user_id=uid
            )
            return

        target_team_id, channel_id = found
        USAGE_STATS["analyze_calls"] += 1
        save_stats()

        try:
            target_client = get_client_for_team(target_team_id)
            card = ProgressCard(
                client=get_client_for_team(target_team_id),
                channel=ch,
                thread_ts=thread,
                title=f"Analyzing #{raw} (channel)"
            )
            card.start("Fetching channel messages…")

            def _run_with_progress(c: WebClient):
                return analyze_entire_channel(
                    c,
                    channel_id,
                    thread,
                    progress_card_cb=lambda pct, note: card.set(pct, note),
                    time_bump=lambda: card.maybe_time_bumps(),
                )

            summary = _run_with_progress(target_client)
            summary = summary.replace("[DD/MM/YYYY HH:MM UTC]", "").replace("*@username*", "").strip()
            card.finish(ok=True, note="Completed.")

            send_message(
                get_client_for_team(target_team_id),
                ch if ch.startswith("D") else ch,
                summary,
                thread_ts=thread,
                user_id=uid,
                export_pdf=True
            )

            _get_memory(thread).save_context(
                {"human_input": f"ANALYZE #{channel_id} (team {target_team_id})"},
                {"output": summary}
            )

        except Exception as e:
            try:
                card.finish(ok=False, note="Failed.")
            except Exception:
                pass
            send_message(
                client, ch,
                (
                    f"❌ *Failed to process channel* `{channel_id}` (team `{target_team_id}`):\n\n"
                    f"`{e}`\n\n"
                    "*Tips:*\n"
                    "• Ensure the bot is invited to that channel in its workspace.\n"
                    "• For private channels, invite the bot explicitly."
                ),
                thread_ts=thread, user_id=uid
            )
        return

    # Thread URL analysis
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)
    if m:
        if not is_followup:
            USAGE_STATS["analyze_calls"] += 1
            ANALYSIS_THREADS.add(thread)
        else:
            USAGE_STATS["analyze_followups"] += 1
        save_stats()

        cid = m.group(1)
        raw = m.group(2)
        ts10 = raw[:10] + "." + raw[10:]
        cmd = normalized.replace(m.group(0), "").strip().lower()

        try:
            export_pdf = False
            card = ProgressCard(client, ch, thread, title="Thread analysis")
            card.start("Fetching Slack messages…")

            def _run_with_progress(c: WebClient):
                if cid in FORMATTED_CHANNELS:
                    return analyze_slack_thread(
                        c, cid, ts10,
                        instructions=cmd,
                        default= True,
                        progress_card_cb=lambda pct, note: card.set(pct, note),
                        time_bump=lambda: card.maybe_time_bumps(),
                    )
                return analyze_slack_thread(
                        c, cid, ts10,
                        instructions=cmd,
                        default=False,
                        progress_card_cb=lambda pct, note: card.set(pct, note),
                        time_bump=lambda: card.maybe_time_bumps(),
                    )

            detected_team = detect_real_team_from_event(None, event)
            target_team_id, summary = ROUTER.try_call(detected_team, _run_with_progress)

            summary = summary.replace("[DD/MM/YYYY HH:MM UTC]", "").replace("*@username*", "").strip()
            card.finish(ok=True)

            send_message(
                get_client_for_team(target_team_id),
                ch,
                summary,
                thread_ts=thread,
                user_id=uid,
                export_pdf=(cid in FORMATTED_CHANNELS)
            )
            send_message(
                get_client_for_team(target_team_id),
                ch,
                "💬 Want a deeper dive? Reply in *this thread* with your question "
                "(e.g., *explain the timeline*, *why did we escalate*, *expand Business Impact*).",
                thread_ts=thread,
                user_id=uid
            )
            _get_memory(thread).save_context(
                {"human_input": f"{cmd.upper() or 'ANALYZE'} {ts10} (team {target_team_id})"},
                {"output": summary}
            )
        except Exception as e:
            try:
                card.finish(ok=False, note="Failed.")
            except Exception:
                pass
            send_message(
                client, ch,
                f"❌ Could not process thread in either workspace: {e}",
                thread_ts=thread, user_id=uid
            )
        return

    # RAG/Excel processing
    if thread in EXCEL_TABLES:
        df = EXCEL_TABLES[thread]
        answer = answer_from_excel_super_dynamic(df, normalized)
        if answer:
            reply = answer
        else:
            vs = THREAD_VECTOR_STORES[thread]
            try:
                retrieved_docs = vs.query(normalized, k=30)
            except Exception:
                retrieved_docs = []
            if retrieved_docs:
                context = "\n".join(doc.page_content for doc in retrieved_docs)
                prompt = (
                    f"You are a helpful data assistant. Here is data from an Excel table:\n"
                    f"{context}\n\n"
                    f"User question: {normalized}\n"
                    "Only answer using the data above. If the answer is not present, say 'I can't find any match in the file.'"
                )
                reply = process_message_mcp(prompt, thread)
            else:
                reply = (
                    "I can't find any match in the file, here is from my memory:\n\n"
                    f"{process_message_mcp(normalized, thread)}"
                )
    else:
        vs = THREAD_VECTOR_STORES.get(thread)
        if vs and vs.index is not None:
            try:
                retrieved_docs = vs.query(normalized, k=3)
            except Exception:
                retrieved_docs = []

            if retrieved_docs:
                rag_lines = []
                for doc in retrieved_docs:
                    fname = doc.metadata.get("file_name", "unknown")
                    idx = doc.metadata.get("chunk_index", 0)
                    snippet = doc.page_content.replace("\n", " ")[:300].strip()
                    rag_lines.append(f"File: *{fname}* (chunk {idx})\n```{snippet}...```")

                rag_context = "\n\n".join(rag_lines)
                final_input = (
                    f"Here are relevant excerpts from the file uploaded in this thread:\n\n"
                    f"{rag_context}\n\nUser: {normalized}"
                )
                reply = process_message_mcp(final_input, thread)
            else:
                reply = (
                    "I can't find any match in that file, here is from my memory:\n\n"
                    f"{process_message_mcp(normalized, thread)}"
                )
        else:
            reply = process_message_mcp(normalized, thread)

    if reply:
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            if thread in ANALYSIS_THREADS:
                USAGE_STATS["analyze_followups"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
        save_stats()

        send_message(
            client, ch, reply,
            thread_ts=thread, user_id=uid
        )





# ─────────────────────────────────────────────────────────────────────────────
# Flow Handlers - Action Item Bot
# ─────────────────────────────────────────────────────────────────────────────
def handle_action_item_flow(event, client):
    """Handle action item bot flow"""
    try:
        text = event.get("text", "").strip()
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts")
        event_ts = event.get("ts")
        user_id = event.get("user")

        # Remove bot mention from text
        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        # Check for DM extraction
        if "extract dm between" in text.lower():
            handle_dm_extraction(event, client)
        # Check for channel extraction
        elif "extract from" in text and "from" in text and "to" in text:
            handle_channel_extraction(event, client)
        # Check for thread extraction
        elif thread_ts:
            handle_thread_extraction(event, client)
        # Check for task management
        elif any(keyword in text.lower() for keyword in ["show my tasks", "my tasks", "show task", "show tasks"]):
            show_user_tasks(user_id, channel_id, thread_ts or event_ts)
        else:
            # Default response with all options
            response_ts = thread_ts or event_ts
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=response_ts,
                text="I can help you extract tasks from:\n\n"
                    "• *Channels*: `@Bot extract from channel_name from YYYY-MM-DD to YYYY-MM-DD`\n"
                    "• *Threads*: Mention me in any thread\n"
                    "• *DMs*: `@Bot extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD`\n\n"
                    "Or say 'show my tasks' to see your pending tasks."
            )

    except Exception as e:
        logger.error(f"Error in handle_action_item_flow: {str(e)}", exc_info=True)
        client.chat_postMessage(
            channel=event.get("channel"),
            thread_ts=event.get("thread_ts") or event.get("ts"),
            text=f"Error processing request: {str(e)}"
        )

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

        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

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

        client.chat_postMessage(
            channel=channel_id,
            thread_ts=event_ts,
            text=f"Extracting messages from #{channel_name} between {start_date_str} and {end_date_str}..."
        )

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

        user_map = {}
        conversation_text = ""
        
        for msg in messages:
            msg_user = msg.get("user")
            msg_text = msg.get("text", "").strip()
            
            if not msg_text or msg.get("bot_id") or "extract from" in msg_text.lower():
                continue
                
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

        logger.info("Generating action items from channel conversation...")
        action_items_text = action_item_generator.generate(conversation_text, context_type="channel")

        action_items = []
        if action_items_text and action_items_text != "No actionable tasks detected.":
            for line in action_items_text.split('\n'):
                line = line.strip()
                if line.startswith('* - [') and ']:' in line:
                    try:
                        user_part, task_part = line.split(']:', 1)
                        user = user_part.replace('* - [', '').strip()
                        task = task_part.strip()
                        
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

        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        logger.info(f"Extracting from thread {thread_ts} in channel {channel_id}")

        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Extracting tasks from this thread..."
        )

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

        user_map = {}
        conversation_text = ""
        for msg in messages:
            msg_user = msg.get("user", "unknown")
            msg_text = msg.get("text", "").strip()
            if msg_text and not msg.get("bot_id"):
                if "extract" in msg_text.lower() and f"<@{BOT_USER_ID}>" in msg_text:
                    continue
                    
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

        logger.info("Generating action items from thread conversation...")
        action_items_text = action_item_generator.generate(conversation_text, context_type="thread")

        action_items = []
        if action_items_text and action_items_text != "No actionable tasks detected.":
            for line in action_items_text.split('\n'):
                line = line.strip()
                if line.startswith('* - [') and ']:' in line:
                    try:
                        user_part, task_part = line.split(']:', 1)
                        user = user_part.replace('* - [', '').strip()
                        task = task_part.strip()
                        
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

        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()

        pattern = r"extract dm (?:between\s+)?(\w+)\s+(\w+)(?:\s+from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2}))?"
        match = re.search(pattern, text, re.IGNORECASE)
        
        if match:
            user1_name, user2_name, start_date_str, end_date_str = match.groups()
            
            if not start_date_str or not end_date_str:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                start_date_str = start_date.strftime("%Y-%m-%d")
                end_date_str = end_date.strftime("%Y-%m-%d")
            else:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

            logger.info(f"Extracting DM between {user1_name} and {user2_name} from {start_date_str} to {end_date_str}")

            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text=f"Extracting DM messages between {user1_name} and {user2_name} from {start_date_str} to {end_date_str}..."
            )

            user1_id, user1_full_name = get_user_id_by_name_part(user1_name)
            user2_id, user2_full_name = get_user_id_by_name_part(user2_name)

            if not user1_id or not user2_id:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"Could not find one or both users. Found: {user1_full_name or 'None'}, {user2_full_name or 'None'}"
                )
                return

            messages = fetch_dm_messages_between_users(user1_name, user2_name)
            
            if not messages:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text=f"No DM channel found between {user1_full_name} and {user2_full_name}"
                )
                return

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

            user_map = {
                user1_id: user1_full_name or user1_name,
                user2_id: user2_full_name or user2_name
            }

            action_items = extract_action_items_llm(filtered_messages, user_map)

            if not action_items or (len(action_items) == 1 and "error" in action_items[0]):
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=event_ts,
                    text="No actionable tasks found in the DMs or there was an error processing them."
                )
                return

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
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=event_ts,
                text="Please use: @Bot extract dm user1 user2 from YYYY-MM-DD to YYYY-MM-DD\nExample: @Bot extract dm sanjay hari from 2025-10-01 to 2025-10-30"
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

# ─────────────────────────────────────────────────────────────────────────────
# Event Handlers - Combined
# ─────────────────────────────────────────────────────────────────────────────
@app.event("app_mention")
def handle_app_mention(body, event, say, client, logger):
    """
    Main app mention handler - routes to appropriate bot flow
    """
    real_team = detect_real_team_from_event(body, event)
    client = get_client_for_team(real_team)
    
    text = event.get("text", "").lower()
    
    # Route based on content
    if any(keyword in text for keyword in ["extract", "task", "todo"]):
        # Route to Action Item Bot
        handle_action_item_flow(event, client)
    else:
        # Route to Thread Analysis Bot
        handle_thread_analysis_flow(client, event, event.get("text", "").strip())

@app.event({"type": "message", "subtype": "file_share"})
def handle_file_share(body, event, client: WebClient, logger):
    """Handle file shares - Thread Analysis Bot functionality"""
    real_team = detect_real_team_from_event(body, event)
    logger.debug(f"Handling file share for team {real_team!r}")
    client = get_client_for_team(real_team)
    files = event.get("files", [])
    if not files:
        return
    file_obj = files[0]
    file_id = file_obj["id"]
    channel_id = event["channel"]
    user_id = event.get("user")
    file_name = file_obj.get("name", "")
    thread_ts = event.get("thread_ts") or event.get("ts")

    supported = {"pdf", "docx", "doc", "txt", "md", "csv", "py", "xlsx", "xls"}
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    
    if ext not in supported:
        send_message(
            client,
            channel_id,
            (
                f"⚠️ Oops—I can't handle *.{ext}* files yet. "
                "Right now I only support:\n"
                "• PDF (.pdf)\n"
                "• Word documents (.docx, .doc)\n"
                "• Plain-text & Markdown (.txt, .md)\n"
                "• CSV files (.csv)\n"
                "• Python scripts (.py)\n"
                "• Excel files (.xlsx, .xls)"
            ),
            thread_ts=thread_ts,
            user_id=user_id
        )
        return

    try:
        resp = client.files_info(file=file_id)
        file_info = resp["file"]
    except SlackApiError as e:
        logger.error(f"files_info failed: {e.response['error']}")
        return

    parent_text = ""
    if "text" in event:
        parent_text = event.get("text", "")
    elif body and "event" in body and "text" in body["event"]:
        parent_text = body["event"].get("text", "")
    
    from utils.file_utils import check_and_handle_innovation_report
    if check_and_handle_innovation_report(ext, parent_text, client, file_info, channel_id, thread_ts, user_id):
        return

    send_message(
        client,
        channel_id,
        f":loadingcircle: Received *{file_info.get('name')}*. Indexing now…",
        thread_ts=thread_ts,
        user_id=user_id
    )

    try:
        local_path = download_slack_file(client, file_info)
        raw_text = extract_text_from_file(local_path)
    except Exception as e:
        logger.exception(f"Error retrieving file {file_id}: {e}")
        send_message(
            client, channel_id,
            f"❌ Failed to download *{file_info.get('name')}*: {e}",
            thread_ts=thread_ts, user_id=user_id
        )
        return

    if ext in ("xlsx", "xls"):
        try:
            df = extract_excel_as_table(local_path)
            EXCEL_TABLES[thread_ts] = df
            docs = dataframe_to_documents(df, file_name)
            if thread_ts not in THREAD_VECTOR_STORES:
                safe_thread = thread_ts.replace(".", "_")
                THREAD_VECTOR_STORES[thread_ts] = FaissVectorStore(
                    index_path=f"data/faiss_{safe_thread}.index",
                    docstore_path=f"data/docstore_{safe_thread}.pkl"
                )
            vs = THREAD_VECTOR_STORES[thread_ts]
            vs.add_documents(docs)
        except Exception as e:
            logger.exception(f"Error parsing Excel file {file_name}: {e}")
            send_message(
                client, channel_id,
                f"❌ Failed to parse Excel file: {e}",
                thread_ts=thread_ts, user_id=user_id
            )

    if not raw_text.strip():
        send_message(
            client, channel_id,
            f"⚠️ I couldn't extract any text from *{file_info.get('name')}*.",
            thread_ts=thread_ts, user_id=user_id
        )
        return

    if thread_ts not in THREAD_VECTOR_STORES:
        safe_thread = thread_ts.replace(".", "_")
        THREAD_VECTOR_STORES[thread_ts] = FaissVectorStore(
            index_path=f"data/faiss_{safe_thread}.index",
            docstore_path=f"data/docstore_{safe_thread}.pkl"
        )
    vs = THREAD_VECTOR_STORES[thread_ts]

    splitter = RecursiveCharacterTextSplitter(chunk_size=5000, chunk_overlap=500)
    chunks = splitter.split_text(raw_text)
    docs = [
        Document(
            page_content=chunk,
            metadata={
                "file_name": file_info.get("name"),
                "file_id": file_id,
                "chunk_index": i
            }
        )
        for i, chunk in enumerate(chunks)
    ]

    logger.debug(f"Starting background indexing for team {real_team}")
    threading.Thread(
        target=index_in_background,
        args=(vs, docs, client, channel_id, thread_ts, user_id, file_info.get("name"), real_team, ext),
        daemon=True
    ).start()

@app.event("message")
def handle_direct_message(body, event, client: WebClient, logger):
    """Handle direct messages - route to appropriate bot"""
    real_team = detect_real_team_from_event(body, event)
    client = get_client_for_team(real_team)
    
    if event.get("subtype"):
        return

    if event.get("channel_type") != "im":
        return

    text = event.get("text", "").strip()
    channel_id = event["channel"]
    user_id = event["user"]
    thread_ts = event.get("ts")

    if not text:
        send_message(
            client, channel_id,
            ":wave: Hi there! I can help you with:\n\n"
            "*Thread Analysis*: Analyze threads, summarize conversations, answer questions\n"
            "*Action Items*: Extract tasks from channels, threads, and DMs\n\n"
            "Just ask me anything!",
            thread_ts=thread_ts, user_id=user_id
        )
        return

    # Route based on content
    if any(keyword in text.lower() for keyword in ["extract", "task", "todo", "show my tasks"]):
        handle_action_item_flow(event, client)
    else:
        handle_thread_analysis_flow(client, event, text)

# ─────────────────────────────────────────────────────────────────────────────
# Action Handlers - Combined
# ─────────────────────────────────────────────────────────────────────────────
@app.action("select_language")
def handle_language_selection(ack, body, logger):
    ack()
    selected = body["actions"][0]["selected_option"]["value"]
    user_id = body["user"]["id"]
    logger.info(f"User {user_id} selected language: {selected}")

@app.action("translate_button")
def handle_translate_click(ack, body, client, logger):
    ack()
    try:
        state_vals = body.get("state", {}).get("values", {}).get("translate_controls", {})
        lang = (
            state_vals.get("select_language", {})
            .get("selected_option", {})
            .get("value", "en")
        )

        orig_blocks = body.get("message", {}).get("blocks", []) or []
        sections = []
        for blk in orig_blocks:
            if blk.get("type") == "section":
                text_obj = blk.get("text") or {}
                if text_obj.get("type") == "mrkdwn" and "text" in text_obj:
                    sections.append(text_obj["text"])
        original_text = "\n".join(sections).strip()

        translated = (translation_chain.invoke({"text": original_text, "language": lang}) or "").strip()
        translated = translated.replace("[DD/MM/YYYY HH:MM UTC]", "").replace("*@username*", "").strip()

        send_message(
            client,
            body["channel"]["id"],
            f":earth_asia: *Translation ({lang}):*\n{translated}",
            thread_ts=body["message"]["ts"],
            user_id=None,
            export_pdf=False,
        )
    except Exception:
        logger.exception("Translation failed")
        client.chat_postMessage(
            channel=body.get("channel", {}).get("id"),
            thread_ts=body.get("message", {}).get("ts"),
            text="❌ Sorry, translation failed."
        )

@app.action("export_pdf")
def handle_export_pdf(ack, body, client, logger):
    ack()
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    thread_ts = body["message"]["ts"]
    summary_md = body["message"]["blocks"][0]["text"]["text"]
    summary_md = resolve_user_mentions(client, summary_md)

    plain = re.sub(r'\r\n?', '\n', summary_md)
    pdf_buffer = render_summary_to_pdf(plain)
    USAGE_STATS["pdf_exports"] += 1
    client.files_upload_v2(
        channels=[channel_id],
        file=pdf_buffer,
        filename="summary.pdf",
        title="Exported Summary",
        thread_ts=thread_ts
    )

@app.action("vote_up")
def handle_vote_up(ack, body, client):
    ack(); _handle_vote(body, client, "up", "👍")

@app.action("vote_down")
def handle_vote_down(ack, body, client):
    ack(); _handle_vote(body, client, "down", "👎")

@app.action(re.compile(r"thumbs_up_feedback_select_\d+"))
def handle_thumbs_up_feedback(ack, body, client):
    global _vote_up_count, _vote_reasons, _feedback_submissions
    ack()

    uid = body["user"]["id"]
    ts = body["message"]["ts"]
    ch = body["channel"]["id"]
    action = body["actions"][0]
    key = f"{ch}-{ts}-{uid}"

    if key in _feedback_submissions:
        client.chat_postMessage(
            channel=ch,
            thread_ts=ts,
            text=f"<@{uid}>, you've already submitted feedback for this message. ✅"
        )
        return
    
    feedback_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    if "selected_option" in action:
        selected_text = action["selected_option"]["text"]["text"]
    elif "value" in action:
        selected_text = action["value"]
    elif "text" in action:
        selected_text = action["text"]["text"]
    else:
        selected_text = "Unknown feedback"

    _vote_up_count += 1
    _vote_reasons.setdefault("up", {})[feedback_time] = selected_text
    _feedback_submissions.add(key)
    save_stats()

    client.chat_postMessage(
        channel=ch,
        thread_ts=ts,
        text=f"<@{uid}>, Thank you for your honest feedback ❤️"
    )

@app.action(re.compile(r"thumbs_down_feedback_select_\d+"))
def handle_thumbs_down_feedback(ack, body, client):
    global _vote_down_count, _vote_reasons, _feedback_submissions
    ack()
    uid = body["user"]["id"]
    ts = body["message"]["ts"]
    ch = body["channel"]["id"]
    action = body["actions"][0]
    key = f"{ch}-{ts}-{uid}"

    if key in _feedback_submissions:
        client.chat_postMessage(
            channel=ch,
            thread_ts=ts,
            text=f"<@{uid}>, you've already submitted feedback for this message. ✅"
        )
        return

    feedback_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    if "selected_option" in action:
        selected_text = action["selected_option"]["text"]["text"]
    elif "value" in action:
        selected_text = action["value"]
    elif "text" in action:
        selected_text = action["text"]["text"]
    else:
        selected_text = "Unknown feedback"

    _vote_down_count += 1
    _vote_reasons.setdefault("down", {})[feedback_time] = selected_text
    _feedback_submissions.add(key)
    save_stats()

    client.chat_postMessage(
        channel=ch,
        thread_ts=ts,
        text=f"<@{uid}>, Thank you for your honest feedback ❤️"
    )

def _handle_vote(body, client, vote_type, emoji):
    global _vote_up_count, _vote_down_count
    uid = body["user"]["id"]
    ts = body["message"]["ts"]
    ch = body["channel"]["id"]
    _vote_registry.setdefault(ts,set())
    _already_warned.setdefault(ts,set())
    if uid in _vote_registry[ts]:
        if uid not in _already_warned[ts]:
            client.chat_postMessage(channel=ch, thread_ts=ts,
                                    text=f"<@{uid}> you've already voted ✅")
            _already_warned[ts].add(uid)
        return
    _vote_registry[ts].add(uid)
    
    send_message(
        client, ch,
        "Thanks for the 👍!" if vote_type == "up" else "Sorry to hear that 👎",
        thread_ts=ts,
        show_thumbs_up_feedback=(vote_type == "up"),
        show_thumbs_down_feedback=(vote_type == "down")
    )

    if vote_type=="up": 
        _vote_up_count+=1
    else: 
        _vote_down_count+=1
    save_stats()

# Action Item Bot action handlers
@app.action("select_tasks_to_delete")
def handle_select_tasks_to_delete(ack, body, logger):
    ack()
    logger.info("Checkbox interaction received")

@app.action("delete_selected_tasks")
def handle_delete_selected_tasks(ack, body, client, say):
    ack()
    user_id = body["user"]["id"]

    selected_tasks = []
    for block in body["state"]["values"].values():
        for action in block.values():
            if action["type"] == "checkboxes":
                selected_tasks = [opt["value"] for opt in action.get("selected_options", [])]

    if not selected_tasks:
        say(text="Please select at least one task to delete.", thread_ts=body["message"]["ts"])
        return

    deleted_count = 0
    for task_id in selected_tasks:
        deleted = delete_task(task_id)
        if deleted:
            deleted_count += 1

    say(
        text=f"Deleted {deleted_count} task(s).",
        thread_ts=body["message"]["ts"]
    )

    show_user_tasks(user_id, body["channel"]["id"], body["message"]["ts"], say)

@app.action(re.compile("task_checkbox_.*"))
def handle_task_checkbox(ack, body, action):
    ack()
    try:
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        user_info = app.client.users_info(user=user_id)
        user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or user_info["user"]["name"]

        selected = action.get("selected_options", [])
        if not selected:
            return

        value = selected[0]["value"]
        assigned_user, task_description, deadline = value.split("|")

        if assigned_user.lower() != user_name.lower():
            app.client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Only {assigned_user} can claim this task."
            )
            return
        
        if check_existing_task(user_id, task_description):
            app.client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"You already claimed this task: {task_description}"
            )
            logger.info(f"Duplicate task prevented for user={user_name}: {task_description}")
            return
        
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

@app.action("claim_task_action")
def handle_claim_task_action(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        user_info = client.users_info(user=user_id)
        user_name = user_info["user"]["profile"]["real_name"]

        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        original_thread_ts = body.get("container", {}).get("thread_ts")

        action = body["actions"][0]

        selected = [opt["value"] for opt in action.get("selected_options", [])]
        previous = [opt["value"] for opt in action.get("initial_options", [])] if action.get("initial_options") else []

        new_selection = list(set(selected) - set(previous))
        if not new_selection:
            logger.info("No new checkbox selected (could be deselection).")
            return

        value = new_selection[0]

        parts = value.split("|")
        if len(parts) >= 3:
            responsible = parts[0].strip() or user_name
            task_description = parts[1].strip()
            deadline = parts[2].strip()
        elif len(parts) == 2:
            responsible = parts[0].strip() or user_name
            task_description = parts[1].strip()
            deadline = ""
        else:
            responsible = user_name
            task_description = value.strip()
            deadline = ""

        if not task_description:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="?? Could not extract task description."
            )
            return

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
                text=(
                    f"? *Task claimed by {user_name}*\n"
                    f"*Task:* {task_description}\n"
                    f"*Deadline:* {deadline or 'No Deadline'}\n"
                    f"*Task ID:* `{task_id}`"
                )
            )
        else:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"? Failed to save task: {task_description}"
            )

    except Exception as e:
        logger.error(f"Error handling claim task: {e}", exc_info=True)
        client.chat_postEphemeral(
            channel=body.get("channel", {}).get("id", ""),
            user=body.get("user", {}).get("id", ""),
            text=f"Error saving task: {str(e)}"
        )

# ─────────────────────────────────────────────────────────────────────────────
# Slash Commands - Combined
# ─────────────────────────────────────────────────────────────────────────────
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
🤖 *Combined Bot Help*

*Thread Analysis Features:*
- Paste a Slack thread URL with keywords like 'analyze', 'summarize', or 'explain'
- Ask questions about uploaded files (PDF, TXT, CSV, XLSX)
- Use `-org` for knowledge base queries
- Get channel summaries with `analyze #channel-name`

*Action Item Features:*
- Extract tasks from channels: `@Bot extract from channel_name from YYYY-MM-DD to YYYY-MM-DD`
- Extract tasks from threads: Mention me in any thread
- Extract tasks from DMs: `@Bot extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD`
- Show your tasks: Say "show my tasks" or use `/show_tasks`

*General Commands:*
`/show_tasks` - Show your pending tasks
`/help` - Show this help message

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
@Bot extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD

Examples:
@Bot extract dm between sanjay hari
@Bot extract dm between alice bob from 2025-10-01 to 2025-10-31

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
@Bot extract from channel_name from YYYY-MM-DD to YYYY-MM-DD

Examples:
@Bot extract from general from 2025-10-01 to 2025-10-31
@Bot extract from project-updates from 2025-09-01 to 2025-09-30
"""
    
    respond(help_text)

@app.command("/extract_all_tasks")
def extract_all_tasks_command(ack, command, respond):
    ack()
    
    try:
        parts = command["text"].strip().split()
        if len(parts) != 2:
            respond("Usage: /extract_all_tasks YYYY-MM-DD YYYY-MM-DD")
            return
        
        start_date, end_date = parts
        
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
        respond("DM extraction is now available via: @Bot extract dm between user1 user2 from YYYY-MM-DD to YYYY-MM-DD")
        
    except Exception as e:
        logger.error(f"Error in extract_all_tasks_command: {e}", exc_info=True)
        respond(f"Error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# App Home & Error Handlers
# ─────────────────────────────────────────────────────────────────────────────
@app.event("app_home_opened")
def update_home_tab(client, event, logger):
    user_id = event["user"]
    try:
        client.views_publish(
            user_id=user_id,
            view={
                "type": "home",
                "callback_id": "home_view",
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text", "text": "🔎 Ask-Support-Bot + Todo Assistant", "emoji": True}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": (
                            "👋 *Welcome!* I'm your combined assistant, here to help you with:\n\n"
                            "• *Thread Analysis* - Summarize conversations, analyze threads, answer questions\n"
                            "• *Action Items* - Extract and manage tasks from channels, threads, and DMs"
                        )
                    }},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn",
                       "text": (
                           "*How it works:*\n\n"
                           "1️⃣  *Thread Analysis:* DM me with keywords like `analyze`, `explain`, or `summarize` followed by:\n\n"
                           "     • Thread URL for thread analysis (eg: `analyze https://example.slack.com/archives/CXXXXXX/p12345678` )\n\n"
                           "     • `#channel-name` for channel analysis(eg: `analyze #channel-name`)\n\n"
                           "2️⃣  *Action Items:* Use extraction commands to get tasks from conversations\n\n"
                           "3️⃣  *Get Results:* Receive structured summaries and task lists"
                       )
                    }},
                    {"type": "divider"},
                    {"type": "section", "block_id": "file_section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Use Case: Document Q&A*\n\n"
                            "Upload PDF, TXT, CSV, or XLSX files in a DM.\n"
                            "Start a thread and ask questions about the document contents."
                        )
                    }},
                    {"type": "divider"},
                    {"type": "section", "block_id": "general_section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Use Case: General Q&A*\n\n"
                            "Ask me anything in a DM or mention me in a channel.\n"
                            "I'll respond based on my training and the latest data."
                        )
                    }},
                    {"type": "divider"},
                    {"type": "section", "block_id": "orgkb_section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Use Case: Persistent Knowledge Base*\n\n"
                            "Access your already-loaded, org-wide knowledge base right from a DM or channel.\n"
                            "Use the `-org` command at the *start* of your message, followed by your question.\n\n"
                            "*What you can do:*\n"
                            "• *Ask a question:* `-org who is the support owner for <ProductName>?`\n\n"
                            "_Tip: Always start with `-org`. In channels, remember to @mention the bot._"
                        )
                    }},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Action Item Features:*\n\n"
                            "• Extract tasks from channels, threads, and DMs\n\n"
                            "• Track and manage your tasks\n\n"
                            "• Claim tasks with interactive checkboxes\n\n"
                            "• Show your pending tasks with `/show_tasks`"
                        )
                    }},
                    {"type": "divider"},
                    {"type": "context", "elements": [
                        {"type": "mrkdwn", "text": "💡 Need help? Type `help` in a DM or use `/help` command."}
                    ]}
                ]
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish home tab for {user_id}: {e}")

@app.error
def global_error_handler(error, body, logger):
    logger.error(f"Error: {error}")
    logger.error(f"Request body: {body}")

# ─────────────────────────────────────────────────────────────────────────────
# Main Execution
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        index_startup_files()
    except Exception as e:
        logger.exception(f"Startup indexing failed: {e}")
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # Get bot user ID dynamically
    try:
        BOT_USER_ID = app.client.auth_test()["user_id"]
        logger.info(f"Bot user ID: {BOT_USER_ID}")
    except Exception as e:
        logger.error(f"Error fetching bot user ID: {str(e)}")
        BOT_USER_ID = None
    
    logger.info("Starting combined Slack bot with thread analysis and action item features...")
    try:
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        handler.start()
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}", exc_info=True)