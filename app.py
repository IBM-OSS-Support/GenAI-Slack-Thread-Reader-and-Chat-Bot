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
logging.basicConfig(level=logging.DEBUG)

# Instantiate a single global vector store
THREAD_VECTOR_STORES: dict[str, FaissVectorStore] = {}
if not os.path.exists("data"):
    os.makedirs("data", exist_ok=True)
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
def index_in_background(vs: FaissVectorStore, docs: list[Document],
                        client: WebClient, channel_id: str,
                        thread_ts: str, user_id: str, filename: str):
    """
    Run vs.add_documents(docs) in a separate thread. When done,
    send a “finished indexing” message into the same thread.
    """
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
@app.event("file_shared")
def handle_file_shared(event, client: WebClient, logger):
    """
    1) Determine the real ts of the file‐message
    2) Download & extract text
    3) Immediately reply “Indexing now…” in that thread
    4) Spawn a background thread that performs vs.add_documents(...) and,
       when finished, sends “✅ Finished indexing…” into the same thread.
    """

    file_id    = event.get("file_id")
    channel_id = event.get("channel_id")
    user_id    = event.get("user_id") or event.get("user")

    # 1️⃣ Fetch file_info so we can find the real message ts in "shares"
    try:
        resp = client.files_info(file=file_id)
        file_info = resp["file"]
    except SlackApiError as e:
        logger.error(f"files_info failed: {e.response['error']}")
        return

    shares = file_info.get("shares", {})
    thread_ts = None

    private_shares = shares.get("private", {})
    if channel_id in private_shares and private_shares[channel_id]:
        thread_ts = private_shares[channel_id][0].get("ts")
    else:
        public_shares = shares.get("public", {})
        if channel_id in public_shares and public_shares[channel_id]:
            thread_ts = public_shares[channel_id][0].get("ts")

    if not thread_ts:
        thread_ts = event.get("event_ts")

    # 2️⃣ Download & extract text
    try:
        local_path = download_slack_file(client, file_info)
        raw_text   = extract_text_from_file(local_path)

        if not raw_text.strip():
            send_message(
                client,
                channel_id,
                f"⚠️ I couldn’t extract any text from *{file_info.get('name')}*.",
                thread_ts=thread_ts,
                user_id=user_id
            )
            return

        # 3️⃣ Build or fetch this thread’s FaissVectorStore
        if thread_ts not in THREAD_VECTOR_STORES:
            safe_thread = thread_ts.replace(".", "_")
            idx_path    = f"data/faiss_{safe_thread}.index"
            doc_path    = f"data/docstore_{safe_thread}.pkl"
            THREAD_VECTOR_STORES[thread_ts] = FaissVectorStore(
                index_path=idx_path,
                docstore_path=doc_path
            )

        vs = THREAD_VECTOR_STORES[thread_ts]

        # Split into 1,000-character chunks
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks   = splitter.split_text(raw_text)

        docs = []
        for i, chunk in enumerate(chunks):
            metadata = {
                "file_name":   file_info.get("name"),
                "file_id":     file_id,
                "chunk_index": i,
            }
            docs.append(Document(page_content=chunk, metadata=metadata))

        # 4️⃣ Immediately acknowledge and tell the user we're indexing
        send_message(
            client,
            channel_id,
            f"⏳ Received *{file_info.get('name')}*. Indexing now (this may take a minute)…",
            thread_ts=thread_ts,
            user_id=user_id
        )

        # 5️⃣ Spawn a background thread to do the heavy work
        bg_thread = threading.Thread(
            target=index_in_background,
            args=(vs, docs, client, channel_id, thread_ts, user_id, file_info.get("name")),
            daemon=True
        )
        bg_thread.start()

    except Exception as e:
        logger.exception(f"Error processing uploaded file: {e}")
        send_message(
            client,
            channel_id,
            f"❌ Failed to process *{file_info.get('name')}*: {e}",
            thread_ts=thread_ts,
            user_id=user_id
        )
@app.event("app_mention")
def handle_app_mention(event,say,client):
    process_conversation(client,event,event.get("text","").strip())

if __name__=="__main__":
    SocketModeHandler(app,SLACK_APP_TOKEN).start()
