from dotenv import load_dotenv
load_dotenv()

import json
import os
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
from utils.slack_tools import get_user_name
from utils.export_pdf import render_summary_to_pdf
SLACK_APP_TOKEN      = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
BOT_USER_ID          = os.getenv("BOT_USER_ID")

TEAM_BOT_TOKENS = {
    os.getenv("TEAM1_ID"): os.getenv("TEAM1_BOT_TOKEN"),
    os.getenv("TEAM2_ID"): os.getenv("TEAM2_BOT_TOKEN"),
}

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
        print(f"⚠️ Missing env var: {name}")
        sys.exit(1)

try:
    _EXPIRATION_SECONDS = int(os.getenv("SESSION_EXPIRATION_SECONDS", "600"))
except ValueError:
    logging.warning("Invalid SESSION_EXPIRATION_SECONDS, defaulting to 600")
    _EXPIRATION_SECONDS = 600
mins = _EXPIRATION_SECONDS // 60

COMMAND_KEYWORDS = {
    # analyze
    "analyze", "analyse", "dissect", "interpret",
    # summarize
    "summarize", "summarise", "recap", "review", "overview",
    # explain
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
    signing_secret=SLACK_SIGNING_SECRET,
    authorize=custom_authorize,
)

STATS_FILE = os.getenv("STATS_FILE", "/data/stats.json")
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
            }, f)
    except:
        logging.exception("Failed to save stats")

_stats           = load_stats()
_unique_users    = _stats["unique_users"]
_vote_up_count   = _stats["thumbs_up"]
_vote_down_count = _stats["thumbs_down"]

_last_activity   = {}
_active_sessions = {}
_command_counts  = {}
_vote_registry   = {}
_already_warned  = {}

# —————————————————————————————————————————————
# Usage Tracking
# —————————————————————————————————————————————
USAGE_STATS = {
    "total_calls": _stats["total_calls"],
    "analyze_calls": _stats["analyze_calls"],
    "analyze_followups": _stats["analyze_followups"],
    "general_calls": _stats["general_calls"],
    "general_followups": _stats["general_followups"],
    "pdf_exports": _stats["pdf_exports"],  # NEW: track PDF exports
}

# NEW: track which threads began as an analysis
ANALYSIS_THREADS: set[str] = set()

def get_channel_name(client: WebClient, channel_id: str) -> str:
    try:
        info = client.conversations_info(channel=channel_id)
        if info.get("ok"):
            return f"#{info['channel']['name']}"
    except SlackApiError:
        logging.exception(f"Failed channel.info for {channel_id}")
    return f"#{channel_id}"

def resolve_user_mentions(client: WebClient, text: str) -> str:
    text = re.sub(r"@<(@?[UW][A-Z0-9]{8,})>", r"<\1>", text)
    text = re.sub(
        r"<@([UW][A-Z0-9]{8,})>",
        lambda m: f"@{get_user_name(client, m.group(1))}",
        text,
    )
    text = re.sub(
        r"\b([UW][A-Z0-9]{8,})\b",
        lambda m: f"@{get_user_name(client, m.group(1))}"
                  if m.group(1).startswith(("U","W")) else m.group(1),
        text,
    )
    text = re.sub(
        r"<#(C[A-Z0-9]{8,})(?:\|[^>]+)?>",
        lambda m: get_channel_name(client, m.group(1)),
        text,
    )
    return text
@app.action("export_pdf")
def handle_export_pdf(ack, body, client, logger):
    ack()
    user_id    = body["user"]["id"]
    channel_id = body["channel"]["id"]
    thread_ts  = body["message"]["ts"]
    summary_md = body["actions"][0]["value"]

    # 1. Convert Slack markdown to plain text:
    #    remove * around headings, collapse multiple spaces
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

def _handle_vote(body, client, vote_type, emoji):
    global _vote_up_count, _vote_down_count
    uid  = body["user"]["id"]
    ts   = body["message"]["ts"]
    ch   = body["channel"]["id"]
    _vote_registry.setdefault(ts,set())
    _already_warned.setdefault(ts,set())
    if uid in _vote_registry[ts]:
        if uid not in _already_warned[ts]:
            client.chat_postMessage(channel=ch, thread_ts=ts,
                                    text=f"<@{uid}> you've already voted ✅")
            _already_warned[ts].add(uid)
        return
    _vote_registry[ts].add(uid)
    if vote_type=="up": _vote_up_count+=1
    else:              _vote_down_count+=1
    save_stats()
    client.chat_postMessage(channel=ch, thread_ts=ts,
                            text=f"Thanks for your feedback {emoji}")

def track_usage(uid, thread_ts, cmd=None):
    global _unique_users
    now=time.time()
    _active_sessions[thread_ts]=now
    _last_activity[thread_ts]=now
    before=len(_unique_users)
    _unique_users.add(uid)
    if len(_unique_users)>before: save_stats()
    if cmd: _command_counts[cmd]=_command_counts.get(cmd,0)+1

def get_bot_stats():
    return (
        "📊 *Bot Usage Stats*\n"
        f"• *Total calls:* {USAGE_STATS['total_calls']}\n"
        f"• *Analyze calls:* {USAGE_STATS['analyze_calls']} (follow-ups: {USAGE_STATS['analyze_followups']})\n"
        f"• *General calls:* {USAGE_STATS['general_calls']} (follow-ups: {USAGE_STATS['general_followups']})\n"
        f"• *PDF exports:* {USAGE_STATS['pdf_exports']}\n\n"
        f"👍 *{_vote_up_count}*   👎 *{_vote_down_count}*"
    )

def process_conversation(client: WebClient, event, text: str):
    ch      = event["channel"]
    ts      = event["ts"]
    thread  = event.get("thread_ts") or ts
    uid     = event["user"]

    # Expiration check
    now = time.time()
    last = _last_activity.get(thread)
    if last and now - last > _EXPIRATION_SECONDS:
        _memories.pop(thread, None)
        _last_activity.pop(thread, None)
        _active_sessions.pop(thread, None)
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

    logging.debug("🔔 Processing: %s", resolve_user_mentions(client, cleaned).strip())

    # Help command
    if resolve_user_mentions(client, cleaned).strip() == "":
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
    # Thread analysis
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)
    if m:
        # if initial analysis → analyze_calls + track thread
        if not is_followup:
            USAGE_STATS["analyze_calls"] += 1
            ANALYSIS_THREADS.add(thread)
        else:
            USAGE_STATS["analyze_followups"] += 1
        save_stats()
        cid, raw = m.group(1), m.group(2)
        ts10     = raw[:10] + "." + raw[10:]
        cmd      = normalized.replace(m.group(0), "").strip().lower()

        try:
            export_pdf = False
            if not cmd or cmd in COMMAND_KEYWORDS:
                summary = analyze_slack_thread(client, cid, ts10)
                export_pdf = True
            else:
                summary = analyze_slack_thread(client, cid, ts10, instructions=cmd)

            out = resolve_user_mentions(client, summary)
            send_message(
                client,
                ch,
                out,
                thread_ts=thread,
                user_id=uid,
                export_pdf=export_pdf
            )
            _get_memory(thread).save_context(
                {"human_input": f"{cmd.upper() or 'ANALYZE'} {ts10}"},
                {"output": summary}
            )
        except Exception as e:
            send_message(
                client, ch,
                f"❌ Could not process thread: {e}",
                thread_ts=thread, user_id=uid
            )
        return

    # Fallback chat
    reply = process_message_mcp(normalized, thread)
    if reply:
        # decide follow-up type
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            if thread in ANALYSIS_THREADS:
                USAGE_STATS["analyze_followups"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
        save_stats()

        out = resolve_user_mentions(client, reply)
        send_message(
            client, ch, out,
            thread_ts=thread, user_id=uid
        )

@app.event("message")
def handle_message_events(event,say,client):
    if event.get("subtype") or event.get("bot_id"): return
    if event["channel"].startswith("D"):
        process_conversation(client,event,event.get("text","").strip())

@app.event("app_mention")
def handle_app_mention(event,say,client):
    process_conversation(client,event,event.get("text","").strip())

if __name__=="__main__":
    SocketModeHandler(app,SLACK_APP_TOKEN).start()
