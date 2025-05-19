import os
import time
import re
import sys
import logging
import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from utils.slack_api import send_message
from chains.chat_chain_mcp import process_message_mcp, _get_memory, _memories
from chains.analyze_thread import analyze_slack_thread
from utils.slack_tools import get_user_name
from chains.stats_store import load_stats, save_stats, add_vote, get_stats


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
_unique_users: set[str] = set()
_command_counts: dict[str, int] = {}
_vote_registry: dict[str, set[str]] = {}
_already_warned: dict[str, set[str]] = {}

# Load initial stats
load_stats()

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

@app.action("vote_up")
def handle_vote_up(ack, body, client):
    _handle_vote(body, client, vote_type="up", emoji="👍")

@app.action("vote_down")
def handle_vote_down(ack, body, client):
    _handle_vote(body, client, vote_type="down", emoji="👎")

def _handle_vote(body, client, vote_type: str, emoji: str):
    user_id = body["user"]["id"]
    message_ts = body["message"]["ts"]
    channel_id = body["channel"]["id"]

    # Initialize structures
    if message_ts not in _vote_registry:
        _vote_registry[message_ts] = set()
    if message_ts not in _already_warned:
        _already_warned[message_ts] = set()

    # Prevent multiple votes
    if user_id in _vote_registry[message_ts]:
        # Only warn once
        if user_id not in _already_warned[message_ts]:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"<@{user_id}> you've already voted on this response. ✅"
            )
            _already_warned[message_ts].add(user_id)
        # Do nothing on further repeated clicks
        return

    # First-time vote
    _vote_registry[message_ts].add(user_id)

    if vote_type == "up":
        add_vote("up", user_id)
    else:
        add_vote("down", user_id)

    client.chat_postMessage(
        channel=channel_id,
        thread_ts=message_ts,
        text=f"Thanks for your feedback {emoji}"
    )

def track_usage(user_id: str, thread_ts: str, command: str = None):
    now = time.time()
    _active_sessions[thread_ts] = now
    _last_activity[thread_ts] = now
    _unique_users.add(user_id)
    if command:
        _command_counts[command] = _command_counts.get(command, 0) + 1

def get_bot_stats() -> str:
    stats = get_stats()
    return (
        f"📊 Bot Usage Stats:\n\n"
        f" Unique users: {stats['unique_users_count']}\n\n"
        f" 👍 Votes: {stats['vote_up_count']}\n\n"
        f" 👎 Votes: {stats['vote_down_count']}\n\n"
    )

def process_conversation(event, text: str):
    channel   = event["channel"]
    ts        = event["ts"]
    thread_ts = event.get("thread_ts")
    invoke_ts = thread_ts or ts
    user_id   = event["user"]

    # Expiration check
    now  = time.time()
    last = _last_activity.get(invoke_ts)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(invoke_ts, None)
        _last_activity.pop(invoke_ts, None)
        _active_sessions.pop(invoke_ts, None)
        send_message(channel_id=channel,
                     text=f"⚠️ Your conversation has expired ({mins} minutes of no activity). Please start a new one.",
                     thread_ts=invoke_ts)
        return

    # Refresh usage
    _last_activity[invoke_ts] = now
    track_usage(user_id, invoke_ts)
    app.logger.debug("🔔 Processing text: %s", resolve_user_mentions(text))

    # Help keyword
    if resolve_user_mentions(text).strip() == BOT_USER_ID:
        help_text = (
            "👋 Hello! Here's how you can use me:\n"
            "- Paste a Slack thread URL with 'analyze', 'summarize', or 'explain'.\n"
            "- Mention me to start a chat.\n"
            "- Reply in a thread to continue with memory."
        )
        send_message(channel_id=channel, text=help_text, thread_ts=invoke_ts)
        return

    # Normalize & Stats
    cleaned    = re.sub(r"<@[^>]+>", "", text).strip()
    normalized = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", cleaned).strip()
    if "stats" in normalized.lower():
        send_message(channel_id=channel, text=get_bot_stats(), thread_ts=invoke_ts)
        return

    # Thread analysis
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)
    if m:
        ch    = m.group(1)
        raw   = m.group(2)
        ts10  = raw[:10] + "." + raw[10:]
        rest  = normalized.replace(m.group(0), "").strip().lower()

        try:
            if not rest or rest in {"analyze", "analyse", "explain", "summarize"}:
                summary = analyze_slack_thread(ch, ts10)
                text_out = resolve_user_mentions(summary.replace("**", "*"))
                send_message(channel_id=channel, text=text_out, thread_ts=invoke_ts)
                _get_memory(invoke_ts).save_context({"human_input": f"ANALYSIS {ts10}"}, {"output": summary})
            else:
                resp = analyze_slack_thread(ch, ts10, instructions=rest)
                text_out = resolve_user_mentions(resp)
                send_message(channel_id=channel, text=text_out, thread_ts=invoke_ts)
                _get_memory(invoke_ts).save_context({"human_input": f"CUSTOM ANALYSIS {ts10}: {rest}"}, {"output": resp})
        except Exception as e:
            send_message(channel_id=channel,
                         text=f"❌ Could not process thread: {e}",
                         thread_ts=invoke_ts)
        return

    # Fallback chat
    reply = process_message_mcp(normalized, invoke_ts)
    if reply:
        send_message(channel_id=channel, text=resolve_user_mentions(reply), thread_ts=invoke_ts)

# Bolt event handlers
@app.event("message")
def handle_message_events(event, say):
    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return
    channel = event["channel"]
    text    = (event.get("text") or "").strip()
    if channel.startswith("D"):
        process_conversation(event, text)

@app.event("app_mention")
def handle_app_mention(event, say):
    text = (event.get("text") or "").strip()
    process_conversation(event, text)

# Start app
if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
