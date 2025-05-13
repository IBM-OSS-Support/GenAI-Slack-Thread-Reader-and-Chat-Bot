from dotenv import load_dotenv
load_dotenv()  # must precede any os.getenv() calls

import os
import time
import re
import sys
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from utils.slack_api import send_message
from chains.chat_chain_mcp import process_message_mcp, _get_memory, _memories
from chains.analyze_thread import analyze_slack_thread
from utils.slack_tools import get_user_name

logging.basicConfig(level=logging.DEBUG)

# Slack tokens (add these to your .env):
# SLACK_BOT_TOKEN: xoxb-...
# SLACK_APP_TOKEN: xapp-...
# BOT_USER_ID:     Your bot's user ID
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
BOT_USER_ID     = os.getenv("BOT_USER_ID")

for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "BOT_USER_ID"):
    if not os.getenv(name):
        print(f"⚠️ Missing env var: {name}")
        sys.exit(1)

app = App(token=SLACK_BOT_TOKEN)

# ——————————————————————————————————————————————
# Idle session tracking: expire after 10 minutes of no activity
try:
    _EXPIRATION_SECONDS = int(os.getenv("SESSION_EXPIRATION_SECONDS", "600"))
except ValueError:
    logging.warning("Invalid SESSION_EXPIRATION_SECONDS, defaulting to 600")
    _EXPIRATION_SECONDS = 600
_last_activity: dict[str, float] = {}
# ——————————————————————————————————————————————

@app.event("message")
def handle_message_events(event, say):
    subtype   = event.get("subtype")
    text      = (event.get("text") or "").strip()
    channel   = event.get("channel")
    ts        = event.get("ts")
    thread_ts = event.get("thread_ts")

    # Ignore any bot messages
    if subtype == "bot_message" or event.get("bot_id"):
        return

    # Only in direct messages
    if channel and channel.startswith("D"):
        invoke_ts = thread_ts or ts

        # —— session-expiration check ——
        now = time.time()
        last = _last_activity.get(invoke_ts)
        if last and now - last > _EXPIRATION_SECONDS:
            # Drop the old memory and expiration record
            _memories.pop(invoke_ts, None)
            _last_activity.pop(invoke_ts, None)
            send_message(
                channel,
                "⚠️ Your conversation has expired (10 minutes of no activity). Please start a new one.",
                invoke_ts
            )
            return
        # Update last‐seen timestamp
        _last_activity[invoke_ts] = now
        # —— end expiration logic ——

        # Normalize any <link|label> to plain URL
        normalized = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", text).strip()
        keywords   = ["analyze", "explain", "summarize", "analyse"]
        match      = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)

        if match and any(kw in normalized.lower() for kw in keywords):
            # ── Thread analysis in DM
            target_channel = match.group(1)
            raw_ts         = match.group(2)
            target_ts      = raw_ts[:10] + "." + raw_ts[10:]

            try:
                summary = analyze_slack_thread(target_channel, target_ts)
                send_message(channel, summary.replace("**", "*"), invoke_ts)
            except Exception as e:
                send_message(
                    channel,
                    f"❌ Could not fetch that thread: {e}\n"
                    "• Invite me to that channel.\n"
                    "• Ensure I have `conversations.replies` & `channels:history` scopes.",
                    invoke_ts
                )
            return

        # ── Fallback to normal DM chat
        reply = process_message_mcp(text, invoke_ts)
        if reply:
            send_message(channel, reply, invoke_ts)
        return

@app.event("app_mention")
def handle_app_mention(event, say):
    text      = (event.get("text") or "").strip()
    channel   = event.get("channel")
    ts        = event.get("ts")
    thread_ts = event.get("thread_ts")
    invoke_ts = thread_ts or ts

    # —— session-expiration check ——
    now = time.time()
    last = _last_activity.get(invoke_ts)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(invoke_ts, None)
        _last_activity.pop(invoke_ts, None)
        send_message(
            channel,
            "⚠️ Your conversation has expired (10 minutes of no activity). Please start a new one.",
            invoke_ts
        )
        return
    _last_activity[invoke_ts] = now
    # —— end expiration logic ——

    # Replace mention with readable name
    pretty_text = re.sub(
        r"<@([A-Z0-9]+)>",
        lambda m: f"{get_user_name(m.group(1))}",
        text
    )
    app.logger.debug("🔔 Received app_mention event: %s", pretty_text)

    # If only the bot was mentioned, show usage
    if pretty_text.strip() == f"{BOT_USER_ID}":
        send_message(
            channel,
            "👋 Hello! Here's how you can use me:\n"
            "- Paste a Slack thread URL along with a keyword like 'analyze', 'summarize', or 'explain' to get a formatted summary of that thread.\n"
            "- Or simply mention me and ask any question to start a chat conversation.\n"
            "- Reply inside a thread to continue the conversation with memory.",
            invoke_ts
        )
        return

    # Strip mentions and unwrap links
    cleaned_text = re.sub(r"<@[^>]+>", "", text).strip()
    normalized   = re.sub(r"<(https?://[^>|]+)(?:\|[^>]+)?>", r"\1", cleaned_text).strip()

    keywords = ["", "analyze", "explain", "summarize", "analyse"]
    match    = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized)

    if match:
        thread_url     = match.group(0)
        target_channel = match.group(1)
        raw_ts         = match.group(2)
        target_ts      = raw_ts[:10] + "." + raw_ts[10:]
        rest           = normalized.replace(thread_url, "").strip()
        rest_lower     = rest.lower()

        # 1️⃣ Generic summary
        if not rest or rest_lower in keywords:
            try:
                summary   = analyze_slack_thread(target_channel, target_ts)
                formatted = summary.replace("**", "*")
                send_message(channel, formatted, invoke_ts)
                memory = _get_memory(invoke_ts)
                memory.save_context(
                    {"human_input": f"ANALYSIS of thread {target_ts}"},
                    {"output": summary}
                )
            except Exception as e:
                send_message(
                    channel,
                    f"❌ Could not fetch that thread: {e}\n"
                    "• Invite the bot to that channel.\n"
                    "• Ensure it has `conversations.replies` & `channels:history` scopes.",
                    invoke_ts
                )
            return

        # 2️⃣ Custom analysis
        try:
            response = analyze_slack_thread(
                target_channel,
                target_ts,
                instructions=rest
            )
            send_message(channel, response, invoke_ts)
            memory = _get_memory(invoke_ts)
            memory.save_context(
                {"human_input": f"CUSTOM ANALYSIS of thread {target_ts}: {rest}"},
                {"output": response}
            )
        except Exception as e:
            send_message(
                channel,
                f"❌ Could not perform custom analysis: {e}",
                invoke_ts
            )
        return

    # 3️⃣ Fallback to normal chat when mentioned
    reply = process_message_mcp(normalized, invoke_ts)
    if reply:
        send_message(channel, reply, invoke_ts)

if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    try:
        handler.start()
    except KeyboardInterrupt:
        print("⚡️ Shutting down…")
        sys.exit(0)
