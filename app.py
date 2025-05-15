from dotenv import load_dotenv
load_dotenv()  # must precede any os.getenv() calls

import os
import time
import re
import sys
import logging
import requests

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from utils.slack_api import send_message
from chains.chat_chain_mcp import process_message_mcp, _get_memory, _memories
from chains.analyze_thread import analyze_slack_thread
from utils.slack_tools import get_user_name

# —————————————————————————————————————————————
# Logging & Env
# —————————————————————————————————————————————
logging.basicConfig(level=logging.DEBUG)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
BOT_USER_ID     = os.getenv("BOT_USER_ID")
for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "BOT_USER_ID"):
    if not os.getenv(name):
        print(f"⚠️ Missing env var: {name}")
        sys.exit(1)

try:
    _EXPIRATION_SECONDS = int(os.getenv("SESSION_EXPIRATION_SECONDS", "600"))
except ValueError:
    logging.warning("Invalid SESSION_EXPIRATION_SECONDS, defaulting to 600")
    _EXPIRATION_SECONDS = 600
mins = _EXPIRATION_SECONDS // 60

app = App(token=SLACK_BOT_TOKEN)

# —————————————————————————————————————————————
# In-memory state
# —————————————————————————————————————————————
_last_activity: dict[str, float] = {}
_active_sessions: dict[str, float] = {}
_unique_users: set[str]     = set()
_command_counts: dict[str,int] = {}

# —————————————————————————————————————————————
# Helpers
# —————————————————————————————————————————————

def get_channel_name(channel_id: str) -> str:
    try:
        resp = requests.get(
            "https://slack.com/api/conversations.info",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"channel": channel_id},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return f"#{data['channel']['name']}"
    except Exception:
        logging.exception(f"Failed to fetch channel info for {channel_id}")
    return f"#{channel_id}"

def resolve_user_mentions(text: str) -> str:
    text = re.sub(r'@<(@?[UW][A-Z0-9]{8,})>', r'<\1>', text)
    text = re.sub(r'<([UW][A-Z0-9]{8,})>', lambda m: f"@{get_user_name(m.group(1))}", text)
    text = re.sub(r'<@([UW][A-Z0-9]{8,})>', lambda m: f"@{get_user_name(m.group(1))}", text)
    text = re.sub(r'@([UW][A-Z0-9]{8,})', lambda m: f"@{get_user_name(m.group(1))}", text)
    text = re.sub(r'<#(C[A-Z0-9]{8,})(?:\|[^>]+)?>', lambda m: get_channel_name(m.group(1)), text)
    return text

def track_usage(user_id: str, thread_ts: str, command: str = None):
    now = time.time()
    _active_sessions[thread_ts]   = now
    _last_activity[thread_ts]     = now
    _unique_users.add(user_id)
    if command:
        _command_counts[command] = _command_counts.get(command, 0) + 1

def get_bot_stats() -> str:
    return (
        f"📊 Bot Usage Stats:\n"
        f"• Unique users: {len(_unique_users)}\n"
    )

def process_conversation(event, say, text: str):
    """
    Shared DM & @mention logic: expiration, stats, analyze, fallback chat.
    """
    channel   = event["channel"]
    ts        = event["ts"]
    thread_ts = event.get("thread_ts")
    invoke_ts = thread_ts or ts
    user_id   = event["user"]

    # — Expiration —
    now  = time.time()
    last = _last_activity.get(invoke_ts)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(invoke_ts, None)
        _last_activity.pop(invoke_ts, None)
        _active_sessions.pop(invoke_ts, None)
        say(
            text=(
                f"⚠️ Your conversation has expired ({mins} minutes of no activity). "
                f"Please start a new one."
            ),
            thread_ts=invoke_ts
        )
        return

    # — Refresh usage & log —
    _last_activity[invoke_ts] = now
    track_usage(user_id, invoke_ts)
    app.logger.debug("🔔 Processing text: %s", resolve_user_mentions(text))

    # — Help keyword —
    if resolve_user_mentions(text).strip() == BOT_USER_ID:
        say(
            text=(
                "👋 Hello! Here's how you can use me:\n"
                "- Paste a Slack thread URL with 'analyze', 'summarize', or 'explain'.\n"
                "- Mention me to start a chat.\n"
                "- Reply in a thread to continue with memory."
            ),
            thread_ts=invoke_ts
        )
        return

    # — Normalize & Stats —
    cleaned    = re.sub(r"<@[^>]+>", "", text).strip()
    normalized = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", cleaned).strip()
    if "stats" in normalized.lower():
        say(text=get_bot_stats(), thread_ts=invoke_ts)
        return

    # — Thread analysis —
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)
    if m:
        ch    = m.group(1)
        raw   = m.group(2)
        ts10  = raw[:10] + "." + raw[10:]
        rest  = normalized.replace(m.group(0), "").strip().lower()

        # default vs custom
        if not rest or rest in {"analyze","analyse","explain","summarize"}:
            try:
                summary = analyze_slack_thread(ch, ts10)
                text_out = resolve_user_mentions(summary.replace("**","*"))
                say(text=text_out, thread_ts=invoke_ts)
                mem = _get_memory(invoke_ts)
                mem.save_context({"human_input":f"ANALYSIS {ts10}"},{"output":summary})
            except Exception as e:
                say(
                    text=(
                        f"❌ Could not fetch that thread: {e}\n"
                        "• Invite me to that channel.\n"
                        "• Ensure `conversations.replies` & `channels:history`."
                    ),
                    thread_ts=invoke_ts
                )
            return

        try:
            resp = analyze_slack_thread(ch, ts10, instructions=rest)
            say(text=resolve_user_mentions(resp), thread_ts=invoke_ts)
            mem = _get_memory(invoke_ts)
            mem.save_context({"human_input":f"CUSTOM ANALYSIS {ts10}: {rest}"},{"output":resp})
        except Exception as e:
            say(text=f"❌ Custom analysis failed: {e}", thread_ts=invoke_ts)
        return

    # — Fallback chat —
    reply = process_message_mcp(normalized, invoke_ts)
    if reply:
        say(text=resolve_user_mentions(reply), thread_ts=invoke_ts)

# —————————————————————————————————————————————
# Bolt event handlers
# —————————————————————————————————————————————

@app.event("message")
def handle_message_events(event, say):
    # ignore other bots
    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return

    channel = event["channel"]
    text    = (event.get("text") or "").strip()

    if channel.startswith("D"):
        # DM:
        thread_ts = event.get("thread_ts")
        process_conversation(event, say, text)

@app.event("app_mention")
def handle_app_mention(event, say):
    text = (event.get("text") or "").strip()
    process_conversation(event, say, text)

# —————————————————————————————————————————————
# Start app
# —————————————————————————————————————————————

if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
