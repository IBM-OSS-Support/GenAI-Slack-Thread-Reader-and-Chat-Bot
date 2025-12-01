# chains/analyze_channel.py
from __future__ import annotations

import os
import re
import time
import json
import logging
import asyncio
from contextlib import contextmanager
from typing import Dict, List, Tuple, Optional, Callable

from datetime import datetime
from zoneinfo import ZoneInfo

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.client import WebClient  # only used if you later need sync utils

# LangChain (chat/text-agnostic)
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

# LLM provider (supports GPT-OSS / ChatOllama / Ollama)
from chains.llm_provider import get_llm
from chains.llm_provider import is_chat_model

# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------
logger = logging.getLogger("channel_analyzer")
logger.setLevel(logging.INFO)
# If you want file logging, uncomment below:
# if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
#     fh = logging.FileHandler("channel_analyzer.log", encoding="utf-8")
#     fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
#     fh.setFormatter(fmt)
#     logger.addHandler(fh)

@contextmanager
def timed(step_name: str, extra: dict | None = None):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        if extra:
            logger.info(f"{step_name} completed in {elapsed:.3f}s | {extra}")
        else:
            logger.info(f"{step_name} completed in {elapsed:.3f}s")

# -----------------------------------------------------------------------------
# Time helpers (IST)
# -----------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")

def _format_date_time_from_ts(ts_str: str) -> Tuple[str, str]:
    """
    Given a Slack TS string (e.g., '1723526482.12345'), return:
      - posted_date: '13 August 2025'
      - posted_time: '20:31 IST'
    """
    try:
        ts = float(ts_str)
    except (TypeError, ValueError):
        return "", ""
    dt = datetime.fromtimestamp(ts, tz=IST)
    return dt.strftime("%d %B %Y"), dt.strftime("%H:%M %Z")

# -----------------------------------------------------------------------------
# LLM config (envs remain; actual selection done via get_llm/is_chat_model)
# -----------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama-dev-unique.apps.epgui.cp.fyre.ibm.com")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")

# -----------------------------------------------------------------------------
# Channel summary prompt (same content, split into SYSTEM/HUMAN for chat)
# -----------------------------------------------------------------------------
CHANNEL_SUMMARY_SYSTEM = (
    "You are a Slack assistant summarizing an internal support or escalation thread."
)

# The HUMAN part embeds your original instructions verbatim (format kept the same).
CHANNEL_SUMMARY_HUMAN = """
Below is the full message history in JSON format, where each message and reply contains:
- thread_id
- user_name
- text
- posted_date (e.g., "11 June 2025")
- posted_time (e.g., "21:13 IST")

Always use the `posted_date` and `posted_time` values when adding timestamps in the *Decisions Made* and *Action Items* sections.
Never guess or alter the year; use exactly what appears in `posted_date`.

{messages}

Produce *exactly five sections*, in this order, using Slack markdown with *bold section titles* (asterisks) and no other formatting:

*Summary*  
- One clear sentence stating what triggered this thread (e.g., “An escalation opened due to Cognos performance degradation.”).

*Business Impact*  
- Only include bullets for impacts *explicitly mentioned* in the thread.  
- Use *exactly* these labels (omit any not present):  
  - *Revenue at risk*: …  
  - *Operational impact*: …  
  - *Customer impact*: …  
  - *Team impact*: …  
  - *Other impacts*: …  
  - *Outstanding risks*: …  ← capture any remaining risks (e.g. missing logs, revenue still at risk)

*Key Points Discussed*  
- One bullet per distinct event, fact, request, or update from the thread.  
- *Include*:  
  - any *to-date fixes & effects* (what was changed, when, and what improvement was seen)  
  - any *key metrics before/after* (CPU %, queue depths, job runtimes)  
- Use speaker names *only* when needed for clarity.  
- Do *not* add anything not present in the messages.

*Decisions Made*  
- List *all* concrete decisions (even logistical ones).  
- *Include* any *next checkpoints* scheduled (date/time and responsible lead).  
- Use the exact `posted_date` and `posted_time` from the relevant message.  
- Format each as:  
  - *@username* decided to … [DD/MM/2025 HH:MM UTC]

*Action Items*  
- List only explicit follow-up tasks assigned to someone.  
- Use the exact `posted_date` and `posted_time` from the relevant message.  
- Format each as:  
  - *@username* to … [DD/MM/2025 HH:MM UTC]

*Do not* invent any bullets, sections, or timestamps. 
If something isn’t in the thread, leave it out—do *not* guess.
"""

# Build chat and text prompt variants
channel_chat_prompt = ChatPromptTemplate.from_messages(
    [("system", CHANNEL_SUMMARY_SYSTEM), ("human", CHANNEL_SUMMARY_HUMAN)]
)
channel_text_prompt = PromptTemplate.from_template(
    "SYSTEM:\n" + CHANNEL_SUMMARY_SYSTEM + "\n\nUSER:\n" + CHANNEL_SUMMARY_HUMAN
)

# Model + parser
llm = get_llm()
parser = StrOutputParser()

# Choose chat vs text chain at runtime
if is_chat_model(llm):
    channel_summary_chain: Runnable = channel_chat_prompt | llm | parser
else:
    channel_summary_chain: Runnable = channel_text_prompt | llm | parser

# -----------------------------------------------------------------------------
# Async Slack helpers
# -----------------------------------------------------------------------------
async def _call_with_retry(func, *args, **kwargs):
    while True:
        try:
            return await func(*args, **kwargs)
        except SlackApiError as e:
            if e.response is not None and e.response.status_code == 429:
                delay = int(e.response.headers.get("Retry-After", "1"))
                logger.warning(
                    f"Rate limited on {getattr(func, '__name__', str(func))}; retrying after {delay}s"
                )
                await asyncio.sleep(delay)
                continue
            logger.error(f"Slack API error on {getattr(func, '__name__', str(func))}: {e}")
            raise

class UserNameCache:
    """Async user/bot display-name cache and mention resolver."""
    MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")

    def __init__(self):
        self._cache: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_name(self, client: AsyncWebClient, user_or_bot_id: str) -> str:
        async with self._lock:
            if user_or_bot_id in self._cache:
                return self._cache[user_or_bot_id]

        name: Optional[str] = None

        # Try users.info
        try:
            resp = await _call_with_retry(client.users_info, user=user_or_bot_id)
            user = resp.get("user")
            if user:
                prof = user.get("profile", {}) or {}
                name = (
                    prof.get("display_name_normalized")
                    or prof.get("real_name_normalized")
                    or prof.get("real_name")
                    or user.get("name")
                )
        except SlackApiError:
            pass

        # Try bots.info if looks like a bot ID
        if not name and user_or_bot_id.startswith("B"):
            try:
                resp = await _call_with_retry(client.bots_info, bot=user_or_bot_id)
                bot = resp.get("bot")
                if bot:
                    name = bot.get("name")
            except SlackApiError:
                pass

        if not name:
            name = user_or_bot_id  # last resort

        async with self._lock:
            self._cache[user_or_bot_id] = name
        return name

    async def replace_mentions(self, client: AsyncWebClient, text: str) -> str:
        """Replace <@UXXXX> with @Display Name using cached lookups."""
        if not text:
            return ""
        ids = list({m.group(1) for m in self.MENTION_RE.finditer(text)})
        if not ids:
            return text

        # fetch all names concurrently (cached)
        names = await asyncio.gather(*(self.get_name(client, uid) for uid in ids))
        id_to_at = {uid: f"@{name}" for uid, name in zip(ids, names)}

        def _sub(m: re.Match) -> str:
            uid = m.group(1)
            return id_to_at.get(uid, m.group(0))

        return self.MENTION_RE.sub(_sub, text)

async def _fetch_history_paginated(
    client: AsyncWebClient,
    channel_id: str,
    limit_per_page: int = 200,
    # optional progress reporting over a percentage segment
    progress_cb: Optional[Callable[[int, str], None]] = None,
    pct_start: int = 10,
    pct_end: int = 50,
    oldest: Optional[float] = None,
    latest: Optional[float] = None,
) -> List[dict]:
    """
    Fetch all parent messages (exclude replies here) from a Slack channel,
    optionally filtered by oldest/latest timestamps.
    """
    parents, cursor, page_count, msg_count = [], None, 0, 0
    with timed("fetch_channel_history"):
        # We won’t know total pages up front; we’ll approximate using a soft cap
        hard_cap = 8000  # used to scale progress updates
        while True:
            resp = await _call_with_retry(
                client.conversations_history,
                channel=channel_id,
                limit=limit_per_page,
                cursor=cursor,
                include_all_metadata=True,
                oldest=str(oldest) if oldest else None,
                latest=str(latest) if latest else None
            )
            messages = resp.get("messages", []) or []
            page_msgs = 0
            for m in messages:
                ts = m["ts"]
                if m.get("thread_ts") and m["thread_ts"] != ts:
                    continue  # skip replies; handled via conversations.replies
                parents.append(m)
                page_msgs += 1
            msg_count += page_msgs
            page_count += 1
            logger.info(f"History page {page_count}: +{page_msgs} parents (cum {msg_count})")

            # smooth progress within [pct_start, pct_end]
            if progress_cb:
                span = max(0, pct_end - pct_start)
                pct = pct_start + min(span, int((msg_count / hard_cap) * span))
                progress_cb(pct, f"Scanning channel history… ({msg_count} messages)")

            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

    parents.sort(key=lambda m: float(m["ts"]))
    # ensure we land on pct_end when history is done
    if progress_cb:
        progress_cb(pct_end, f"History scanned. ({len(parents)} parent messages)")
    return parents

async def _fetch_replies_for_parent(
    client: AsyncWebClient,
    channel_id: str,
    parent_ts: str,
    limit_per_page: int = 200,
) -> List[dict]:
    out, cursor, pages = [], None, 0
    while True:
        resp = await _call_with_retry(
            client.conversations_replies,
            channel=channel_id,
            ts=parent_ts,
            limit=limit_per_page,
            cursor=cursor,
            include_all_metadata=True
        )
        msgs = resp.get("messages", []) or []
        if pages == 0 and msgs:
            msgs = msgs[1:]  # strip the parent itself on the first page
        out.extend(msgs)
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        pages += 1
        if not cursor:
            break

    out.sort(key=lambda m: float(m["ts"]))
    logger.info(f"Fetched {len(out)} replies for ts={parent_ts} in {pages} page(s)")
    return out

async def _fetch_all_replies_concurrent(
    client: AsyncWebClient,
    channel_id: str,
    parents: List[dict],
    max_concurrency: int = 12,
) -> Dict[str, List[dict]]:
    sem = asyncio.Semaphore(max_concurrency)

    async def worker(m: dict) -> Tuple[str, List[dict]]:
        rc = int(m.get("reply_count", 0) or 0)
        if rc == 0:
            return m["ts"], []
        async with sem:
            replies = await _fetch_replies_for_parent(client, channel_id, m["ts"])
            return m["ts"], replies

    with timed("fetch_all_replies", extra={"parents": len(parents)}):
        pairs = await asyncio.gather(*[worker(m) for m in parents])

    replies_map: Dict[str, List[dict]] = {ts: replies for ts, replies in pairs}
    total_replies = sum(len(v) for v in replies_map.values())
    logger.info(
        f"Collected replies: {total_replies} across "
        f"{len([p for p in parents if int(p.get('reply_count',0) or 0)>0])} parents"
    )
    return replies_map

# -----------------------------------------------------------------------------
# Retry-on-empty mechanics (aligned with analyze_thread)
# -----------------------------------------------------------------------------
from tenacity import retry, wait_random_exponential, stop_after_attempt, retry_if_exception_type

class EmptyLLMOutput(RuntimeError):
    pass

def _trim_messages_blob(s: str, max_chars: int = 6000) -> str:
    """Keep the tail of the JSON string (often where newest content is)."""
    if not isinstance(s, str):
        return s
    if len(s) <= max_chars:
        return s
    tail = s[-max_chars:]
    nl = tail.find("\n")
    return tail[nl+1:] if nl != -1 else tail

@retry(
    wait=wait_random_exponential(min=0.7, max=2.5),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(EmptyLLMOutput),
)
def _invoke_chain(chain: Runnable, /, **inputs) -> str:
    """
    Invoke the chain; if the model returns an empty string, raise to trigger a retry.
    On the 2nd attempt we trim the messages blob (to dodge ctx/decoding edge cases).
    """
    attempt = getattr(_invoke_chain, "_attempt", 1)

    # 1) First try with original inputs
    out = chain.invoke(inputs)
    text = (out or "").strip()
    if text:
        _invoke_chain._attempt = 1  # reset
        return text

    # 2) Empty → try again with a trimmed blob (only once per call stack)
    msg_key = "messages" if "messages" in inputs else ("text" if "text" in inputs else None)
    if msg_key and attempt == 1 and isinstance(inputs[msg_key], str):
        logger.warning("LLM returned empty output; retrying with trimmed JSON messages blob.")
        trimmed = _trim_messages_blob(inputs[msg_key], max_chars=6000)
        new_inputs = dict(inputs)
        new_inputs[msg_key] = trimmed
        _invoke_chain._attempt = 2
        out2 = chain.invoke(new_inputs)
        text2 = (out2 or "").strip()
        if text2:
            _invoke_chain._attempt = 1
            return text2

    # 3) Still empty → raise to trigger tenacity retry
    _invoke_chain._attempt = attempt + 1
    raise EmptyLLMOutput("Model returned empty output")

# -----------------------------------------------------------------------------
# Public async API
# -----------------------------------------------------------------------------
async def analyze_entire_channel_async(
    token: str,
    channel_id: str,
    thread_ts: str,  # API parity; not used in full-channel mode
    # optional progress/ticker callbacks (match thread progress API)
    progress_card_cb: Optional[Callable[[int, str], None]] = None,
    time_bump: Optional[Callable[[], None]] = None,
    oldest: Optional[float] = None,
    latest: Optional[float] = None
) -> str:
    """
    Analyze all messages in a Slack channel within an optional timeframe.
    """
    def step(p: int, msg: str):
        if progress_card_cb:
            try:
                progress_card_cb(max(0, min(100, int(p))), msg)
            except Exception:
                pass

    total_start = time.perf_counter()
    logger.info(f"Starting analyze_entire_channel(async) | channel_id={channel_id} thread_ts={thread_ts}")

    # 0) Start
    step(5, "Preparing channel analysis…")

    client = AsyncWebClient(token=token)
    name_cache = UserNameCache()

    # 1) Fetch parent messages (with optional timeframe)
    parents = await _fetch_history_paginated(
        client,
        channel_id,
        limit_per_page=200,
        progress_cb=progress_card_cb,
        pct_start=10,
        pct_end=50,
        oldest=oldest,
        latest=latest
    )
    if not parents:
        logger.warning(f"No messages found in <#{channel_id}>.")
        step(100, "No messages found.")
        return f":warning: No messages found in <#{channel_id}>."

    # 2) Fetch replies concurrently
    step(55, "Collecting thread replies…")
    replies_map = await _fetch_all_replies_concurrent(client, channel_id, parents, max_concurrency=12)

    # 3) Resolve names + mentions; build minimal JSON
    step(62, "Compiling content…")
    with timed("build_minimal_json", extra={
        "parents": len(parents),
        "total_replies": sum(len(v) for v in replies_map.values())
    }):
        # Parent speaker names
        parent_name_tasks = [
            name_cache.get_name(client, (m.get("user") or m.get("bot_id") or "<unknown>"))
            for m in parents
        ]
        parent_names = await asyncio.gather(*parent_name_tasks)

        # Parent text mention normalization
        parent_text_tasks = [name_cache.replace_mentions(client, m.get("text", "") or "") for m in parents]
        parent_texts = await asyncio.gather(*parent_text_tasks)

        # Replies: speaker names + mention normalization
        reply_name_tasks: List[asyncio.Task] = []
        reply_text_tasks: List[asyncio.Task] = []
        reply_keys: List[Tuple[str, int]] = []  # (parent_ts, idx) to preserve order

        for m in parents:
            pts = m["ts"]
            replies = replies_map.get(pts, [])
            for idx, r in enumerate(replies):
                reply_keys.append((pts, idx))
                reply_name_tasks.append(
                    name_cache.get_name(client, (r.get("user") or r.get("bot_id") or "<unknown>"))
                )
                reply_text_tasks.append(
                    name_cache.replace_mentions(client, r.get("text", "") or "")
                )

        reply_names = await asyncio.gather(*reply_name_tasks) if reply_name_tasks else []
        reply_texts = await asyncio.gather(*reply_text_tasks) if reply_text_tasks else []

        # Re-assemble into ordered minimal JSON
        minimal: List[Dict[str, object]] = []
        reply_name_map: Dict[Tuple[str, int], str] = {
            k: reply_names[i] for i, k in enumerate(reply_keys)
        } if reply_names else {}
        reply_text_map: Dict[Tuple[str, int], str] = {
            k: reply_texts[i] for i, k in enumerate(reply_keys)
        } if reply_texts else {}

        def _format_date_time_from_ts(ts: str) -> Tuple[str, str]:
            from datetime import datetime
            dt = datetime.fromtimestamp(float(ts))
            return dt.strftime("%d %B %Y"), dt.strftime("%H:%M %Z")

        for p_idx, m in enumerate(parents):
            parent_ts = m["ts"]
            posted_date, posted_time = _format_date_time_from_ts(parent_ts)

            rec: Dict[str, object] = {
                "thread_id": parent_ts,
                "user_name": parent_names[p_idx],
                "text": parent_texts[p_idx],
                "posted_date": posted_date,   # 'DD Month 2025'
                "posted_time": posted_time,   # 'HH:MM IST'
            }

            rs: List[Dict[str, str]] = []
            for r_idx, r in enumerate(replies_map.get(parent_ts, [])):
                r_ts = r.get("ts") or parent_ts
                r_date, r_time = _format_date_time_from_ts(r_ts)
                rs.append({
                    "thread_id": parent_ts,  # parent thread id
                    "user_name": reply_name_map.get((parent_ts, r_idx)) or (r.get("user") or r.get("bot_id") or "<unknown>"),
                    "text": reply_text_map.get((parent_ts, r_idx)) or (r.get("text", "") or ""),
                    "posted_date": r_date,
                    "posted_time": r_time,
                })
            if rs:
                rec["replies"] = rs
            minimal.append(rec)

    # 4) Prepare JSON string for LLM (prompt explicitly asks for JSON input)
    with timed("prepare_llm_json"):
        json_input = json.dumps(minimal, ensure_ascii=False)

    # 5) Run model with a gentle ticker for perceived progress
    step(70, "Running analysis…")
    start = time.time()

    def _ticker():
        # raise perceived progress while LLM is thinking
        while True:
            if time_bump:
                try:
                    time_bump()
                except Exception:
                    pass
            # stop nudging after ~12s
            if time.time() - start > 12:
                break
            time.sleep(0.5)

    t = None
    if time_bump:
        import threading
        t = threading.Thread(target=_ticker, daemon=True)
        t.start()

    with timed("llm_summary"):
        try:
            # Use retrying invoke (aligned with analyze_thread)
            result = await asyncio.to_thread(_invoke_chain, channel_summary_chain, messages=json_input)
            step(100, "Completed.")
        except Exception as e:
            logger.error(f"Failed to summarize channel <#{channel_id}>: {e}")
            step(100, "Failed during model call.")
            result = f"❌ Failed to summarize channel <#{channel_id}>: {e}"

    total_elapsed = time.perf_counter() - total_start
    logger.info(
        f"analyze_entire_channel finished | channel_id={channel_id} "
        f"parents={len(parents)} replies={sum(len(v) for v in replies_map.values())} "
        f"total_time={total_elapsed:.3f}s"
    )
    return result

# -----------------------------------------------------------------------------
# Persistence helper (unchanged; optional if you re-enable persistence)
# -----------------------------------------------------------------------------
async def _persist_min_json(min_records: List[Dict[str, object]], channel_id: str) -> str:
    """Persist minimal JSON to disk with fallback to /tmp if needed."""
    def _write(records: List[Dict[str, object]]) -> str:
        if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            logger.addHandler(sh)

        cwd = os.getcwd()
        logger.info(f"Persisting minimal JSON | cwd={cwd}")
        pref_dir = os.getenv("CHANNEL_ANALYZER_OUT_DIR", "summaries")
        ts_str = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        channel_safe = re.sub(r"[^A-Za-z0-9_-]", "_", channel_id)
        filename = f"{channel_safe}_{ts_str}.json"

        try_dirs = [pref_dir]
        if os.path.abspath(pref_dir) != "/tmp/summaries":
            try_dirs.append("/tmp/summaries")

        last_err = None
        for d in try_dirs:
            try:
                os.makedirs(d, exist_ok=True)
                outpath = os.path.abspath(os.path.join(d, filename))
                with open(outpath, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, separators=(",", ":"), indent=None)
                logger.info(f"Minimal JSON written to: {outpath}")
                return outpath
            except PermissionError as e:
                last_err = e
                logger.warning(f"Permission denied for dir '{d}', trying next fallback…")
            except Exception as e:
                last_err = e
                logger.error(f"Failed writing to '{d}': {e}")

        raise RuntimeError(f"Could not write minimal JSON. Last error: {last_err}")

    return await asyncio.to_thread(_write, min_records)

# -----------------------------------------------------------------------------
# Sync wrapper (backward-compatible; optional callbacks supported)
# -----------------------------------------------------------------------------
def analyze_entire_channel(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    progress_card_cb: Optional[Callable[[int, str], None]] = None,
    time_bump: Optional[Callable[[], None]] = None,
    oldest: Optional[float] = None,
    latest: Optional[float] = None
) -> str:
    token = getattr(client, "token", None)
    if not token:
        raise ValueError("WebClient missing token")
    return asyncio.run(
        analyze_entire_channel_async(
            token=token,
            channel_id=channel_id,
            thread_ts=thread_ts,
            progress_card_cb=progress_card_cb,
            time_bump=time_bump,
            oldest=oldest,
            latest=latest
        )
    )
