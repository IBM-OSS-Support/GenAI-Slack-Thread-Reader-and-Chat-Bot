from dotenv import load_dotenv
load_dotenv()

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
from utils.file_utils import download_slack_file, extract_text_from_file
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.vector_store import FaissVectorStore
from utils.vector_store import FaissVectorStore
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.thread_store import THREAD_VECTOR_STORES
from chains.analyze_thread import translation_chain
logging.basicConfig(level=logging.DEBUG)


# Instantiate a single global vector store
# THREAD_VECTOR_STORES: dict[str, FaissVectorStore] = {}
if not os.path.exists("data"):
    os.makedirs("data", exist_ok=True)
SLACK_APP_TOKEN      = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
BOT_USER_ID          = os.getenv("BOT_USER_ID")

TEAM_BOT_TOKENS = {
    os.getenv("TEAM1_ID"): os.getenv("TEAM1_BOT_TOKEN"),
    os.getenv("TEAM2_ID"): os.getenv("TEAM2_BOT_TOKEN"),
}
formatted = os.getenv("FORMATTED_CHANNELS", "")
FORMATTED_CHANNELS = {ch.strip() for ch in formatted.split(",") if ch.strip()}
logging.info(f"Formatted channels: {FORMATTED_CHANNELS}")
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
DEFAULT_TEAM_ID = next(iter(TEAM_BOT_TOKENS))
PLACEHOLDER_TOKEN = TEAM_BOT_TOKENS[DEFAULT_TEAM_ID]
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
    token=PLACEHOLDER_TOKEN,          # ← placeholder to satisfy Bolt
    signing_secret=SLACK_SIGNING_SECRET,
    authorize=custom_authorize,       # ← still do per-event auth here
)

def get_client_for_team(team_id: str) -> WebClient:
    bot_token = TEAM_BOT_TOKENS.get(team_id)
    logging.debug(f"Getting client for team {team_id!r} with token {bot_token!r}")
    if not bot_token:
        raise RuntimeError(f"No token for team {team_id!r}")
    return WebClient(token=bot_token)

STATS_FILE = os.getenv("STATS_FILE", "/data/stats.json")
def index_in_background(vs: FaissVectorStore, docs: list[Document],
                        client: WebClient, channel_id: str,
                        thread_ts: str, user_id: str, filename: str,real_team: str):
    """
    Run vs.add_documents(docs) in a separate thread. When done,
    send a “finished indexing” message into the same thread.
    """
    # 2) rebind your client
    client = get_client_for_team(real_team)
    try:
        # You can optionally log progress inside add_documents itself,
        # but here we just call it and wait.
        vs.add_documents(docs)

        # After indexing completes, send the follow-up message:
        send_message(
            client,
            channel_id,
            f"✅ Finished indexing *{filename}*. What would you like to know?",
            thread_ts=thread_ts,
            user_id=user_id
        )
    except Exception as e:
        # If something goes wrong, notify in thread:
        send_message(
            client,
            channel_id,
            f"❌ Failed to finish indexing *{filename}*: {e}",
            thread_ts=thread_ts,
            user_id=user_id
        )
@app.action("select_language")
def handle_language_selection(ack, body, logger):
    ack()
    selected = body["actions"][0]["selected_option"]["value"]
    user_id = body["user"]["id"]
    logger.info(f"User {user_id} selected language: {selected}")

@app.action("translate_button")
def handle_translate_click(ack, body, client, logger):
    # 1) Ack
    ack()

    # 2) Language choice
    state_vals = body["state"]["values"]["translate_controls"]
    lang = state_vals["select_language"]["selected_option"]["value"]

    # 3) Reconstruct original markdown text
    orig_blocks = body["message"]["blocks"]
    original_text = "\n".join(
        blk["text"]["text"]
        for blk in orig_blocks
        if blk.get("type") == "section"
           and isinstance(blk.get("text"), dict)
           and blk["text"].get("type") == "mrkdwn"
    )

    # 4) Translate via LLMChain
    translated = translation_chain.run(text=original_text, language=lang)

    send_message(
        client,
        body["channel"]["id"],
        f":earth_asia: *Translation ({lang}):*\n{translated}",
        body["message"]["ts"],
        None,
        False,  # NEW: allow PDF export of translations
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
        logging.exception("Failed to save stats")

_stats           = load_stats()
_unique_users    = _stats["unique_users"]
_vote_up_count   = _stats["thumbs_up"]
_vote_down_count = _stats["thumbs_down"]
_vote_reasons = {
    "up": _stats.get("feedback_up_reasons", {}) if isinstance(_stats.get("feedback_up_reasons"), dict) else {},
    "down": _stats.get("feedback_down_reasons", {}) if isinstance(_stats.get("feedback_down_reasons"), dict) else {}
}
_feedback_submissions = set()

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
@app.action("export_pdf")
def handle_export_pdf(ack, body, client, logger):
    ack()
    user_id    = body["user"]["id"]
    channel_id = body["channel"]["id"]
    thread_ts  = body["message"]["ts"]
    # summary_md = body["message"]["text"]
    # summary_md = body["actions"][0]["value"]
    summary_md = body["message"]["blocks"][0]["text"]["text"]

    

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
    
    # Generate timestamp
    feedback_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

     # Safe extraction of selected text
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

    # Generate timestamp
    feedback_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # Safe extraction of selected text
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

    # Thread analysis
    m_ch = re.match(
        r'^(?:analyze|analyse|summarize|summarise|explain)\s+<?#?([A-Za-z0-9_-]+)(?:\|[^>]*)?>?$',
        normalized,
        re.IGNORECASE
    )
    if m_ch:
        raw = m_ch.group(1)
        # raw could be an ID (starts with C…) or a name
        if raw.startswith("C") and raw.isupper():
            channel_id = raw
        else:
            # lookup by name
            resp = client.conversations_list(types="public_channel,private_channel", limit=1000)
            chans = resp.get("channels", [])
            match = next((c for c in chans if c["name"] == raw), None)
            logging.debug(f"Channel match: {match}")
            if not match:
                send_message(
                    client, ch,
                    f"❌ No channel named *{raw}* found. Use the channel’s real name (without ‘#’).",
                    thread_ts=thread, user_id=uid
                )
                return
            channel_id = match["id"]

        USAGE_STATS["analyze_calls"] += 1
        save_stats()
        try:
            summary = analyze_entire_channel(client, channel_id, thread)
            # out = resolve_user_mentions(client, summary)
            send_message(
                client,
                ch,
                summary,
                thread_ts=thread,
                user_id=uid,
                export_pdf=True
            )
            _get_memory(thread).save_context(
                {"human_input": f"ANALYZE #{channel_id}"},
                {"output": summary}
            )
        except Exception as e:
            send_message(
                client, ch,
                (
        f"❌ *Failed to process channel* `{channel_id}`:\n"
        f">\n\n"
        "*🛠️ How to troubleshoot:*\n\n"
        " 🔍 Make sure you’re in the *same workspace* as the channel you’re targeting.\n\n"
        " 📨 If you’re DM’ing the bot, double-check you’ve selected the *correct workspace* from the app’s top menu.\n\n"
        " 🆔 Confirm the channel ID or name is *accurate* and the bot has been *invited*.\n\n"
        "If you still run into issues, please review your app configuration or contact your workspace admin."
    ),
                thread_ts=thread, user_id=uid
            )
        return
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", normalized, re.IGNORECASE)
    if m:
        # if initial analysis → analyze_calls + track thread
        if not is_followup:
            USAGE_STATS["analyze_calls"] += 1
            ANALYSIS_THREADS.add(thread)
        else:
            USAGE_STATS["analyze_followups"] += 1
        save_stats()
        cid    = m.group(1)
        raw    = m.group(2)
        ts10   = raw[:10] + "." + raw[10:]
        cmd    = normalized.replace(m.group(0), "").strip().lower()
        logging.debug(
            "🔗 Analyzing thread %s in channel %s with command '%s'",ts10,cid,cmd)

        try:
            export_pdf = False
            if cid in FORMATTED_CHANNELS:
                summary = analyze_slack_thread(client, cid, ts10,instructions=cmd, default=False)
                export_pdf = True
            else:
                summary = analyze_slack_thread(client, cid, ts10, instructions=cmd, default=True)

            # out = resolve_user_mentions(client, summary)
            send_message(
                client,
                ch,
                summary,
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

    # ── Fallback chat with RAG lookup ──
    # 1) Do a vector search only if FAISS index exists
    final_input = normalized
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
                idx   = doc.metadata.get("chunk_index", 0)
                snippet = doc.page_content.replace("\n", " ")[:300].strip()
                rag_lines.append(f"File: *{fname}* (chunk {idx})\n```{snippet}...```")

            rag_context = "\n\n".join(rag_lines)
            final_input = (
                f"Here are relevant excerpts from the file uploaded in this thread:\n\n"
                f"{rag_context}\n\nUser: {normalized}"
            )

    # 2️⃣ Pass the (possibly‐augmented) prompt into your existing chain
    reply = process_message_mcp(final_input, thread)
    if reply:
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            if thread in ANALYSIS_THREADS:
                USAGE_STATS["analyze_followups"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
        save_stats()

        # out = resolve_user_mentions(client, reply)
        send_message(
            client, ch, reply,
            thread_ts=thread, user_id=uid
        )
@app.event({"type": "message", "subtype": "file_share"})
def handle_file_share(body,event, client: WebClient, logger):
    real_team = (
        body.get("team_id")
        # fallback if you’re using authorizations array:
        or (body.get("authorizations") or [{}])[0].get("team_id")
    )
    logging.debug(f"Handling file share for team {real_team!r}")
    # 2) rebind your client
    client = get_client_for_team(real_team)
    files = event.get("files", [])
    if not files:
        return
    file_obj   = files[0]
    file_id    = file_obj["id"]
    channel_id = event["channel"]
    user_id    = event.get("user")
    file_name  = file_obj.get("name", "")
    supported = {"pdf", "docx", "doc", "txt", "md", "csv", "py"}
    ext = file_name.rsplit(".", 1)[-1].lower()
    thread_ts = event.get("thread_ts") or event.get("ts")

    # 1️⃣ Check against supported extensions
    supported = {"pdf", "docx", "doc", "txt", "md", "csv", "py"}
    ext = file_name.rsplit(".", 1)[-1].lower()
    if ext not in supported:
        send_message(
            client,
            channel_id,
            (
            f"⚠️ Oops—I can’t handle *.{ext}* files yet. "
            "Right now I only support:\n"
            "• PDF (.pdf)\n"
            "• Word documents (.docx, .doc)\n"
            "• Plain-text & Markdown (.txt, .md)\n"
            "• CSV files (.csv)\n"
            "• Python scripts (.py)"
        ),
            thread_ts=thread_ts,
            user_id=user_id
        )
        return

    try:
        resp      = client.files_info(file=file_id)
        file_info = resp["file"]
    except SlackApiError as e:
        logger.error(f"files_info failed: {e.response['error']}")
        return
    
    local_path = None
    try:
        local_path = download_slack_file(client, file_info)
        raw_text   = extract_text_from_file(local_path)
    except Exception as e:
        logger.exception(f"Error retrieving file {file_id}: {e}")
        send_message(client, channel_id, f"❌ Failed to download *{file_info.get('name')}*: {e}", thread_ts=event.get("ts"), user_id=user_id)
        return

    if not raw_text.strip():
        send_message(client, channel_id, f"⚠️ I couldn’t extract any text from *{file_info.get('name')}*.", thread_ts=event.get("ts"), user_id=user_id)
        return

    thread_ts = event.get("thread_ts") or event["ts"]
    if thread_ts not in THREAD_VECTOR_STORES:
        safe_thread = thread_ts.replace(".", "_")
        THREAD_VECTOR_STORES[thread_ts] = FaissVectorStore(
            index_path=f"data/faiss_{safe_thread}.index", docstore_path=f"data/docstore_{safe_thread}.pkl"
        )
    vs = THREAD_VECTOR_STORES[thread_ts]

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks   = splitter.split_text(raw_text)
    docs     = [Document(page_content=chunk, metadata={"file_name": file_info.get("name"),"file_id": file_id,"chunk_index": i}) for i, chunk in enumerate(chunks)]

    send_message(client, channel_id, f"⏳ Received *{file_info.get('name')}*. Indexing now…", thread_ts=thread_ts, user_id=user_id)
    logging.debug("d jdnjdnjdnjdnjkdn   %s",real_team)
    threading.Thread(target=index_in_background, args=(vs, docs, client, channel_id, thread_ts, user_id, file_info.get("name"),real_team), daemon=True).start()

# App mention handler: handles mentions and routes file uploads if present
@app.event("message")
def handle_direct_message(event, client: WebClient, logger):
    real_team = (
        event.get("team")
        or event.get("authorizations", [{}])[0].get("team")
    )
    # 2) rebind your client
    client = get_client_for_team(real_team)
    # ignore messages with subtypes (e.g. file_share, bot_message, etc.)
    if event.get("subtype"):
        return

    # only handle IM (direct message) channels
    if event.get("channel_type") != "im":
        return

    # now your normal chat flow
    text       = event.get("text", "").strip()
    channel_id = event["channel"]
    user_id    = event["user"]
    thread_ts  = event.get("ts")

    # if you want the “help on empty text” behavior:
    if not text:
        send_message(
            client, channel_id,
            ":wave: Hi there! Just mention me in a channel or ask me something right here.",
            thread_ts=thread_ts, user_id=user_id
        )
        return

    # hand off to your RAG/chat engine exactly as you do in handle_app_mention
    process_conversation(client, event, text)
@app.event("app_mention")
def handle_app_mention(event, say, client, logger):
    real_team = (
        event.get("team")
        or event.get("authorizations", [{}])[0].get("team")
    )
    # 2) rebind your client
    client = get_client_for_team(real_team)
    # If a file is attached during the mention, treat it as file_share
    if event.get("files"):
        return handle_file_share(event, client, logger)
    # Otherwise, normal conversation
    process_conversation(client, event, event.get("text", "").strip())

if __name__=="__main__":
    SocketModeHandler(app,SLACK_APP_TOKEN).start()
