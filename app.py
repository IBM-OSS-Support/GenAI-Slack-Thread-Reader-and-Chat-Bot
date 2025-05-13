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

logging.basicConfig(level=logging.DEBUG)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
BOT_USER_ID     = os.getenv("BOT_USER_ID")

for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "BOT_USER_ID"):
    if not os.getenv(name):
        print(f"⚠️ Missing env var: {name}")
        sys.exit(1)

app = App(token=SLACK_BOT_TOKEN)

try:
    _EXPIRATION_SECONDS = int(os.getenv("SESSION_EXPIRATION_SECONDS", "600"))
except ValueError:
    logging.warning("Invalid SESSION_EXPIRATION_SECONDS, defaulting to 600")
    _EXPIRATION_SECONDS = 600

mins = _EXPIRATION_SECONDS // 60
_last_activity: dict[str, float] = {}
_active_sessions: dict[str, float] = {}
_unique_users: set[str] = set()
_command_counts: dict[str, int] = {}

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
    # Fix malformed mentions like @<@UXXXX> or @<UXXXX>
    text = re.sub(r'@<(@?[UW][A-Z0-9]{8,})>', r'<\1>', text)

    # Handle malformed <UXXXXXXX> without @ (rare but needs catching)
    text = re.sub(r'<([UW][A-Z0-9]{8,})>', lambda m: f"@{get_user_name(m.group(1))}", text)

    # Replace user mentions like <@UXXXX> or <@WXXXX>
    text = re.sub(r'<@([UW][A-Z0-9]{8,})>', lambda m: f"@{get_user_name(m.group(1))}", text)

    # Replace bare user IDs like @UXXXX or @WXXXX
    text = re.sub(r'@([UW][A-Z0-9]{8,})', lambda m: f"@{get_user_name(m.group(1))}", text)

    # Replace channel mentions like <#CXXXX|channel-name>
    text = re.sub(r'<#(C[A-Z0-9]{8,})(?:\\|[^>]+)?>', lambda m: get_channel_name(m.group(1)), text)

    return text

def track_usage(user_id: str, thread_ts: str, command: str = None):
    now = time.time()
    _active_sessions[thread_ts] = now
    _last_activity[thread_ts] = now
    _unique_users.add(user_id)
    if command:
        _command_counts[command] = _command_counts.get(command, 0) + 1

def get_bot_stats() -> str:
    now = time.time()
    active_count = sum(1 for ts in _last_activity.values() if now - ts <= _EXPIRATION_SECONDS)
    stats = (
        f"📊 Bot Usage Stats:\n"
        f"• Unique users: {len(_unique_users)}\n"
        f"• Live sessions: {active_count}\n"
    )
    return stats

@app.event("message")
def handle_message_events(event, say):
    subtype   = event.get("subtype")
    text      = (event.get("text") or "").strip()
    channel   = event.get("channel")
    ts        = event.get("ts")
    thread_ts = event.get("thread_ts")
    user_id   = event.get("user")

    if subtype == "bot_message" or event.get("bot_id"):
        return

    if channel and channel.startswith("D"):
        invoke_ts = thread_ts or ts
        now = time.time()
        last = _last_activity.get(invoke_ts)
        if last and now - last > _EXPIRATION_SECONDS:
            _memories.pop(invoke_ts, None)
            _last_activity.pop(invoke_ts, None)
            _active_sessions.pop(invoke_ts, None)
            send_message(channel, "⚠️ Your conversation has expired ({mins} minutes of no activity). Please start a new one.", invoke_ts)
            return

        normalized = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", text).strip()
        keywords   = ["analyze", "explain", "summarize", "analyse"]
        match      = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)

        if user_id:
            track_usage(user_id, invoke_ts, command="analyze" if any(kw in normalized.lower() for kw in keywords) else "chat")

        if match and any(kw in normalized.lower() for kw in keywords):
            target_channel = match.group(1)
            raw_ts         = match.group(2)
            target_ts      = raw_ts[:10] + "." + raw_ts[10:]
            try:
                summary = analyze_slack_thread(target_channel, target_ts)
                formatted = resolve_user_mentions(summary.replace("**", "*"))
                send_message(channel, formatted, invoke_ts)
            except Exception as e:
                send_message(channel, f"❌ Could not fetch that thread: {e}\n• Invite me to that channel.\n• Ensure I have `conversations.replies` & `channels:history` scopes.", invoke_ts)
            return

        reply = process_message_mcp(text, invoke_ts)
        if reply:
            resolved_reply = resolve_user_mentions(reply)
            send_message(channel, resolved_reply, invoke_ts)
        return

@app.event("app_mention")
def handle_app_mention(event, say):
    text      = (event.get("text") or "").strip()
    channel   = event.get("channel")
    ts        = event.get("ts")
    thread_ts = event.get("thread_ts")
    invoke_ts = thread_ts or ts
    user_id   = event.get("user")

    now = time.time()
    last = _last_activity.get(invoke_ts)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(invoke_ts, None)
        _last_activity.pop(invoke_ts, None)
        _active_sessions.pop(invoke_ts, None)
        send_message(channel, "⚠️ Your conversation has expired (10 minutes of no activity). Please start a new one.", invoke_ts)
        return
    _last_activity[invoke_ts] = now

    if user_id:
        track_usage(user_id, invoke_ts)

    pretty_text = resolve_user_mentions(text)
    app.logger.debug("🔔 Received app_mention event: %s", pretty_text)

    if pretty_text.strip() == f"{BOT_USER_ID}":
        send_message(channel, "👋 Hello! Here's how you can use me:\n- Paste a Slack thread URL with 'analyze', 'summarize', or 'explain'.\n- Mention me to start a chat.\n- Reply in a thread to continue with memory.", invoke_ts)
        return

    cleaned_text = re.sub(r"<@[^>]+>", "", text).strip()
    normalized   = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", cleaned_text).strip()

    if "stats" in normalized.lower():
        stats = get_bot_stats()
        send_message(channel, stats, invoke_ts)
        return

    keywords = ["", "analyze", "explain", "summarize", "analyse"]
    match    = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized)

    if match:
        thread_url     = match.group(0)
        target_channel = match.group(1)
        raw_ts         = match.group(2)
        target_ts      = raw_ts[:10] + "." + raw_ts[10:]
        rest           = normalized.replace(thread_url, "").strip()
        rest_lower     = rest.lower()

        if not rest or rest_lower in keywords:
            try:
                summary = analyze_slack_thread(target_channel, target_ts)
                formatted = resolve_user_mentions(summary.replace("**", "*"))
                send_message(channel, formatted, invoke_ts)
                memory = _get_memory(invoke_ts)
                memory.save_context({"human_input": f"ANALYSIS of thread {target_ts}"}, {"output": summary})
            except Exception as e:
                send_message(channel, f"❌ Could not fetch that thread: {e}\n• Invite the bot to that channel.\n• Ensure it has `conversations.replies` & `channels:history` scopes.", invoke_ts)
            return

        try:
            response = analyze_slack_thread(target_channel, target_ts, instructions=rest)
            resolved_response = resolve_user_mentions(response)
            send_message(channel, resolved_response, invoke_ts)
            memory = _get_memory(invoke_ts)
            memory.save_context({"human_input": f"CUSTOM ANALYSIS of thread {target_ts}: {rest}"}, {"output": response})
        except Exception as e:
            send_message(channel, f"❌ Could not perform custom analysis: {e}", invoke_ts)
        return

    reply = process_message_mcp(normalized, invoke_ts)
    if reply:
        resolved_reply = resolve_user_mentions(reply)
        send_message(channel, resolved_reply, invoke_ts)

if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    try:
        handler.start()
    except KeyboardInterrupt:
        print("⚡️ Shutting down…")
        sys.exit(0)
