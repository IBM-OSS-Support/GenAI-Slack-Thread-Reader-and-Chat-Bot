from dotenv import load_dotenv

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
# ─────────────────────────────────────────────────────────────────────────────
# Multi‑workspace router with automatic fallback
# ─────────────────────────────────────────────────────────────────────────────
class WorkspaceRouter:
    def __init__(self, team_tokens: dict[str, str]):
        # keep a stable default: first non-empty token found
        self.team_tokens = {k: v for k, v in team_tokens.items() if k and v}
        if not self.team_tokens:
            raise RuntimeError("No workspace tokens configured!")
        self.default_team_id = next(iter(self.team_tokens.keys()))
        self._clients: dict[str, WebClient] = {}

    def get_client(self, team_id: str | None) -> WebClient:
        tid = team_id or self.default_team_id
        tok = self.team_tokens.get(tid)
        if not tok:
            # fall back to default if unknown team id shows up
            tid = self.default_team_id
        if tid not in self._clients:
            self._clients[tid] = WebClient(token=self.team_tokens[tid])
        return self._clients[tid]

    def iter_clients_with_priority(self, primary_team_id: str | None):
        """Yield (team_id, client) starting with primary if present, then others."""
        seen = set()
        order = []
        if primary_team_id and primary_team_id in self.team_tokens:
            order.append(primary_team_id)
            seen.add(primary_team_id)
        # add the rest deterministically
        for tid in self.team_tokens:
            if tid not in seen:
                order.append(tid)
        for tid in order:
            yield tid, self.get_client(tid)

    # ------------- Helpers that try both workspaces automatically -------------
    def find_channel_anywhere(self, raw: str) -> tuple[str, str] | None:
        """
        Accepts either a channel ID (Cxxxx) or a name (no '#').
        Returns (team_id, channel_id) if found in any workspace.
        """
        if raw.startswith("C") and raw.isupper():
            # It's an ID; try to locate which workspace has it
            for tid, client in self.iter_clients_with_priority(None):
                try:
                    client.conversations_info(channel=raw)
                    return tid, raw
                except SlackApiError:
                    continue
            return None

        # Lookup by name across workspaces
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
        """
        Run a callable that takes a WebClient as first arg.
        Try primary workspace first; on failure, try others.
        Returns (team_id, result). Raises the last error if all fail.
        """
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
    # best‑effort team detection
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

def git_md_to_slack_md(text: str) -> str:
    # **bold** → *bold*
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

# def get_client_for_team(team_id: str) -> WebClient:
#     bot_token = TEAM_BOT_TOKENS.get(team_id)
#     logging.debug(f"Getting client for team {team_id!r} with token {bot_token!r}")
#     if not bot_token:
#         raise RuntimeError(f"No token for team {team_id!r}")
#     return WebClient(token=bot_token)

STATS_FILE = os.getenv("STATS_FILE", "/data/stats.json")
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
    translated = translation_chain.run(text=original_text, language=lang).replace("[DD/MM/YYYY HH:MM UTC]", "").replace("*@username*", "").strip()

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
    summary_md = resolve_user_mentions(client, summary_md)

    

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
    normalized = normalized.replace("’","'").replace("‘","'").replace("“",'"').replace("”",'"')
    m_prod = re.match(r"^-\s*(?:g\s+)?product\s+(.+)$", normalized, re.IGNORECASE)
    if m_prod:
        product_query = m_prod.group(1).strip()
        # Try to build a deterministic profile from Excel tables
        profile_text = get_product_profile(product_query, thread)
        if profile_text:
            # count as "general" usage (consistent with your -org branch)
            if not is_followup:
                USAGE_STATS["general_calls"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
            save_stats()

            send_message(client, ch, profile_text, thread_ts=thread, user_id=uid)
            return
        else:
            # Fallback: ask global KB as a natural question
            # (this leverages your existing RAG + LLM grounding)
            reply = query_global_kb(f"full_product_profile::{product_query}", thread)
            if not is_followup:
                USAGE_STATS["general_calls"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
            save_stats()

            send_message(client, ch, reply, thread_ts=thread, user_id=uid)
            return
    m_kb = re.match(r"^(?:-org|-org:|-askorg|-ask:)\s*(.+)$", normalized, re.IGNORECASE)
    if m_kb:
        question = m_kb.group(1).strip()

        # NEW: pre-analyze the question (spelling/clarity only; JSON-guardrailed; no hallucinations)
        from chains.preanalyze import preanalyze_question
        question = preanalyze_question(question)
        reply = query_global_kb(question, thread)

        # existing stats pattern (keep exactly as you use it for general Q&A)
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            USAGE_STATS["general_followups"] += 1
        save_stats()

        send_message(client, ch, reply, thread_ts=thread, user_id=uid)
        return

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

        # Try to locate the channel across BOTH workspaces
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

        # Run analysis using the correct workspace client
        try:
            target_client = get_client_for_team(target_team_id)
            summary = analyze_entire_channel(target_client, channel_id, thread)\
                .replace("[DD/MM/YYYY HH:MM UTC]", "")\
                .replace("*@username*", "")\
                .strip()
            summary = git_md_to_slack_md(summary)
            send_message(
                get_client_for_team(target_team_id),  # send from that workspace
                ch if ch.startswith("D") else ch,     # DM channel OK; if public, original ch
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

            def _run(c: WebClient):
                # choose default vs formatted based on your toggle
                if cid in FORMATTED_CHANNELS:
                    return analyze_slack_thread(c, cid, ts10, instructions=cmd, default=False)
                return analyze_slack_thread(c, cid, ts10, instructions=cmd, default=True)

            # Try primary team first, then the other workspace(s)
            detected_team = detect_real_team_from_event(None, event)
            target_team_id, summary = ROUTER.try_call(detected_team, _run)

            summary = summary.replace("[DD/MM/YYYY HH:MM UTC]", "").replace("*@username*", "").strip()
            send_message(
                get_client_for_team(target_team_id),
                ch,  # reply in the same DM/thread the user is in
                summary,
                thread_ts=thread,
                user_id=uid,
                export_pdf=(cid in FORMATTED_CHANNELS)
            )
            _get_memory(thread).save_context(
                {"human_input": f"{cmd.upper() or 'ANALYZE'} {ts10} (team {target_team_id})"},
                {"output": summary}
            )
        except Exception as e:
            send_message(
                client, ch,
                f"❌ Could not process thread in either workspace: {e}",
                thread_ts=thread, user_id=uid
            )
        return
    
# -------- Starts: Modified RAG Logic with Excel Table Lookup Handler --------

    # --- Excel Table Q&A ---
    if thread in EXCEL_TABLES:
        df = EXCEL_TABLES[thread]
        answer = answer_from_excel_super_dynamic(df, normalized)
        if answer:
            reply = answer
        else:
            # Fallback to RAG/LLM as before
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
        # --- Your existing RAG logic for other files ---
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
                reply = process_message_mcp(final_input, thread)
            else:
                reply = (
                    "I can't find any match in that file, here is from my memory:\n\n"
                    f"{process_message_mcp(normalized, thread)}"
                )
        else:
            reply = process_message_mcp(normalized, thread)

    #  reply for the excel table or RAG lookup
    if reply:
        # Track usage and stats
        if not is_followup:
            USAGE_STATS["general_calls"] += 1
        else:
            if thread in ANALYSIS_THREADS:
                USAGE_STATS["analyze_followups"] += 1
            else:
                USAGE_STATS["general_followups"] += 1
        save_stats()

        # sent the reply to the user
        send_message(
            client, ch, reply,
            thread_ts=thread, user_id=uid
        )
# -------- Ends: Modified RAG Logic with Excel Table Lookup Handler --------

# ── File share handler ──
# Replace your handle_file_share function with this corrected version:

@app.event({"type": "message", "subtype": "file_share"})
def handle_file_share(body, event, client: WebClient, logger):
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

    # Supported file types
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

    # --- Fetch file info from Slack FIRST ---
    try:
        resp = client.files_info(file=file_id)
        file_info = resp["file"]
    except SlackApiError as e:
        logger.error(f"files_info failed: {e.response['error']}")
        return

    # --- Check for Innovation Report ---
    parent_text = ""
    # Try to get text from the event that triggered this file share
    if "text" in event:
        parent_text = event.get("text", "")
    # Also check if there's a parent message in the body
    elif body and "event" in body and "text" in body["event"]:
        parent_text = body["event"].get("text", "")
    
    # Use the new function from file_utils
    from utils.file_utils import check_and_handle_innovation_report
    if check_and_handle_innovation_report(ext, parent_text, client, file_info, channel_id, thread_ts, user_id):
        return

    # --- Send "Indexing now..." message for regular files ---
    send_message(
        client,
        channel_id,
        f":loadingcircle: Received *{file_info.get('name')}*. Indexing now…",
        thread_ts=thread_ts,
        user_id=user_id
    )

    # --- Download and process the file ---
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

    # --- Excel-specific logic for regular Excel processing ---
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

    # --- For all files: fallback to text chunking for RAG ---
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

# App mention handler: handles mentions and routes file uploads if present
@app.event("message")
def handle_direct_message(body,event, client: WebClient, logger):
   # pick the real workspace:
    real_team = detect_real_team_from_event(body, event)

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
def handle_app_mention(body, event, say, client, logger):
    real_team = detect_real_team_from_event(body, event)

    # 2) rebind your client
    client = get_client_for_team(real_team)
    # If a file is attached during the mention, treat it as file_share
    if event.get("files"):
        # Pass body as well to handle_file_share
        return handle_file_share(body, event, client, logger)
    # Otherwise, normal conversation
    process_conversation(client, event, event.get("text", "").strip())

def do_analysis(body,event: dict, client: WebClient):
    real_team = detect_real_team_from_event(body, event)

    process_conversation(client, event, event["text"])
    # 2) rebind your client
    client = get_client_for_team(real_team)
    # If a file is attached during the mention, treat it as file_share
    if event.get("files"):
        return handle_file_share(event, client)
    # Otherwise, normal conversation
    process_conversation(client, event, event.get("text", "").strip())


@app.action("select_channel_to_join")
def handle_conversation_select(ack, body, client, logger):
    
    ack()

    user_id = body["user"]["id"]
    channel_id = body["actions"][0]["selected_conversation"]

    try:
        if channel_id.startswith("C"):
            # Public channel → bot can join itself
            client.conversations_join(channel=channel_id)

        else:
            # Private channel (ID starts with "G") → invite the bot user
            bot_user_id = client.auth_test()["user_id"]
            client.conversations_invite(
                channel=channel_id,
                users=bot_user_id
            )

        # Success message into that channel
        client.chat_postMessage(
            channel=channel_id,
            text="👋 Thanks for adding me!"
        )

    except SlackApiError as e:
        error_code = e.response["error"]
        logger.error(f"couldn’t add me to {channel_id}: {error_code}")

        # Let the user know in that same channel via ephemeral
        # send the error as a DM to the user
        client.chat_postMessage(
    channel=user_id,
    text=f":x: I wasn’t able to add me to <#{channel_id}>: `{error_code}`"
)
@app.action("analyze_button")
def handle_analyze_button(ack, body, client, logger):
    # 1️⃣ Acknowledge right away so Slack doesn’t complain
    ack()

    try:
        # 2️⃣ Pull the selected channel ID from the conversations_select
        state_values = body["view"]["state"]["values"]
        channel_id   = state_values["channel_input"]["channel_select"]["selected_conversation"]

        # 3️⃣ Build your “fake” message event to kick off the same analysis flow
        action_ts = body["actions"][0]["action_ts"]
        fake_event = {
            "type":    "message",
            "user":    body["user"]["id"],
            "text":    f"analyze <#{channel_id}>",
            "channel": body["user"]["id"],
            "ts":      action_ts,
        }

        # 4️⃣ Hand it off to your unified analysis routine
        do_analysis(fake_event, client)

    except Exception as e:
        logger.error(f"Error in analyze_button handler: {e}")
        # (optional) notify the user:
        client.chat_postMessage(
            channel=body["user"]["id"],
            text=":warning: Oops, something went wrong trying to analyze that channel."
        )
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
                    # Header
                    {"type": "header", "text": {"type": "plain_text", "text": "🔎 Ask-Support-Bot", "emoji": True}},
                    {"type": "divider"},

                    # Welcome section
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": (
                            "👋 *Welcome!* I'm your *Ask-Support-Bot*, here to help you with all your support needs."
                        )
                    }},
                    {"type": "divider"},

                    # How it works
{"type": "section", "text": {"type": "mrkdwn",
   "text": (
       "*How it works:*\n\n"
       "1️⃣  *Chat Method:* DM me with keywords like `analyze`, `explain`, or `summarize` followed by:\n\n"
       "     • Thread URL for thread analysis (eg: `analyze https://example.slack.com/archives/CXXXXXX/p12345678` )\n\n"
       "     • `#channel-name` for channel analysis(eg: `analyze #channel-name`)\n\n"
    #    "2️⃣  *App Home Method:* Use the forms below to paste URLs or select channels directly.\n\n"
    #    "3️⃣  *Get Results:* Receive structured summaries in your DMs."
   )
}},
                    {"type": "divider"},

                    # Invite instructions
                    {"type": "section", "block_id": "invite_info", "text": {"type": "mrkdwn",
                        "text": (
                            "*Invite me to a channel:*\n\n"
                            # "• *Public:* use the selector below.\n\n"
                            # *Private:* 
                            "• Type `/invite @Ask-Support` or mention me in the channel."
                        )
                    }},
                    {"type": "divider"},

                    # Public channel selector
                    # {"type": "section", "text": {"type": "mrkdwn",
                    #     "text": "➕ *Add me to a public channel:*"
                    # }},
                    # {"type": "actions", "block_id": "public_invite", "elements": [
                    #     {
                    #         "type": "conversations_select",
                    #         "action_id": "select_channel_to_join",
                    #         "placeholder": {"type": "plain_text", "text": "Select a channel…", "emoji": True},
                    #         "filter": {"include": ["public"]}
                    #     }
                    # ]},
                    # {"type": "divider"},

                    # Use Case: Analyze Thread
                    # {"type": "section", "block_id": "thread_section", "text": {"type": "mrkdwn",
                    #     "text": (
                    #         "*Use Case: Analyze a Thread*\n\n"
                    #         "Paste a thread URL in the box below or mention me + URL, then click *Analyze Thread*."
                    #     )
                    # }},
                    # {"type": "input", "block_id": "thread_input", "element": {
                    #     "type": "plain_text_input",
                    #     "action_id": "thread_url_input",
                    #     "placeholder": {"type": "plain_text", "text": "Paste thread URL here..."}
                    # }, "label": {"type": "plain_text", "text": "Thread URL"}},
                    # {"type": "actions", "block_id": "thread_actions", "elements": [
                    #     {"type": "button", "text": {"type": "plain_text", "text": "🚀 Analyze Thread"}, "style": "primary", "action_id": "analyze_thread_button"}
                    # ]},
                    # {"type": "divider"},

                    # # Use Case: Analyze Channel
                    # {"type": "section", "block_id": "channel_section", "text": {"type": "mrkdwn",
                    #     "text": (
                    #         "*Use Case: Analyze a Channel*\n\n"
                    #         "Type `analyze #channel-name` in DM or select below, then click *Analyze Channel*."
                    #     )
                    # }},
                    # {"type": "actions", "block_id": "channel_input_block", "elements": [
                    #     {
                    #         "type": "conversations_select",
                    #         "action_id": "analyze_channel_select",
                    #         "placeholder": {"type": "plain_text", "text": "Select a channel…"},
                    #         "filter": {"include": ["public", "private"]}
                    #     },
                    #     {"type": "button", "text": {"type": "plain_text", "text": "🚀 Analyze Channel"}, "style": "primary", "action_id": "analyze_channel_button"}
                    # ]},
                    # {"type": "divider"},

                    # Use Case: Document Q&A
                    {"type": "section", "block_id": "file_section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Use Case: Document Q&A*\n\n"
                            "Upload PDF, TXT, CSV, or XLSX files in a DM.\n"
                            "Start a thread and ask questions about the document contents."
                        )
                    }},
        
                    {"type": "divider"},

                    # Use Case: General Q&A
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
        "_Tip: Always start with `-org`. In channels, remember to @mention the bot (e.g., `@Ask-Support -org ...`). "
        "In a DM, mentioning isn’t required._"
    )
}},
{"type": "divider"},

                    # Features summary
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": (
                            "*Features at a glance:*\n\n"
                            "• Thread & channel summarization\n\n"
                            "• PDF/TXT/CSV/XLSX parsing & Q&A\n\n"
                            "• Multi-language translation\n\n"
                            "• Export summaries as PDF\n\n"
                            "• Instant chat responses"
                        )
                    }},
                    {"type": "divider"},
                        {"type": "divider"},
                

    # FAQ Section
    {"type": "header", "text": {"type": "plain_text", "text": "Frequently Asked Questions", "emoji": True}},
    
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Q1. I tried to analyze a thread or channel, but it's not working.*\n"
                "Make sure the bot has been **invited to that channel** first. "
                "Without being a member, the bot cannot access messages or perform analysis. "
                "Invite it using `/invite @Ask-Support`."
            )
        }
    },
    {"type": "divider"},

    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Q2. I uploaded a file, but it didn’t give a proper response.*\n"
                "Currently, the bot supports **PDF, TXT, CSV, and XLSX** files only. "
                "Other file formats like DOCX or PPTX are not yet supported — stay tuned for future updates."
            )
        }
    },
    {"type": "divider"},

    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Q3. I asked a question in a channel, but the bot didn’t reply.*\n"
                "*When messaging **in a channel**, always **@mention the bot** "
                "(e.g., `@Ask-Support summarize this thread`). "
                "In DMs, you don’t need to mention it. "
                "In thread replies inside a channel, also ensure you mention the bot to trigger its response."
            )
        }
    },

    {"type": "divider"},

                    # Footer / Help
                    # {"type": "context", "elements": [
                    #     {"type": "mrkdwn", "text": (
                    #         "💡 Need help? Type `help` in a DM or visit <https://example.com/docs|our docs>."
                    #     )}
                    # ]}
                ]
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish home tab for {user_id}: {e}")
# Public invite handler remains the same
@app.action("select_channel_to_join")
# def handle_conversation_select(ack, body, client, logger):
#     ack()
#     user_id = body["user"]["id"]
#     channel_id = body["actions"][0]["selected_conversation"]
#     try:
#         if channel_id.startswith("C"):
#             client.conversations_join(channel=channel_id)
#         else:
#             bot_id = client.auth_test()["user_id"]
#             client.conversations_invite(channel=channel_id, users=bot_id)
#         client.chat_postMessage(channel=channel_id, text="👋 Hey! I’m here to help track tasks.")
#         client.chat_postMessage(channel=user_id, text=f"✅ I’ve been added to <#{channel_id}>")
#     except SlackApiError as e:
#         logger.error(e)
#         client.chat_postMessage(channel=user_id, text=f":x: Couldn’t add me: `{e.response['error']}`")

# Analyze Thread button
@app.action("analyze_thread_button")
def handle_analyze_thread_button(ack, body, client, logger):
    ack()
    user = body["user"]["id"]
    url = body["view"]["state"]["values"]["thread_input"]["thread_url_input"]["value"].strip()
    m = re.search(r"https://[^/]+/archives/([^/]+)/p(\d+)", url)
    if not m:
        return client.chat_postMessage(channel=user, text=":x: Invalid thread URL.")
    fake = {"type":"message","user":user,"text":url,"channel":user,"ts":body["actions"][0]["action_ts"]}
    do_analysis(fake, client)

# Analyze Channel button
@app.action("analyze_channel_button")
def handle_analyze_channel_button(ack, body, client, logger):
    ack()
    user = body["user"]["id"]
    cid = body["view"]["state"]["values"]["channel_input_block"]["analyze_channel_select"]["selected_conversation"]
    fake = {"type":"message","user":user,"text":f"analyze <#{cid}>","channel":user,"ts":body["actions"][0]["action_ts"]}
    do_analysis(fake, client)

@app.action("button_click")
def handle_button_click(ack, body, client, logger):
    ack()
    user = body["user"]["id"]
    try:
        client.chat_postMessage(channel=user, text="You clicked the button! 🎉")
    except Exception as e:
        logger.error(f"Error responding to button click: {e}")


if __name__=="__main__":
    try:
        index_startup_files()
    except Exception as e:
        logging.exception(f"Startup indexing failed: {e}")
    threading.Thread(target=run_health_server, daemon=True).start()
    SocketModeHandler(app,SLACK_APP_TOKEN).start()
