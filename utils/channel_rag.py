import os
import re
import time
import json
import logging
import asyncio
from contextlib import contextmanager
from typing import Dict, List, Tuple, Optional

from datetime import datetime
from zoneinfo import ZoneInfo

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.client import WebClient  # only used if you later need sync utils

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

from chains.llm_provider import get_llm

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────
logger = logging.getLogger("channel_analyzer")
logger.setLevel(logging.INFO)
# if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    # fh = logging.FileHandler("channel_analyzer.log", encoding="utf-8")
    # fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    # fh.setFormatter(fmt)
    # logger.addHandler(fh)

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

# ─────────────────────────────────────────────────────────────────────────────
# Time helpers (IST)
# ─────────────────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────────────────
# LLM (unchanged, still available if you want to call it later)
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama-dev-unique.apps.epgui.cp.fyre.ibm.com")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")

llm = get_llm()
channel_prompt = PromptTemplate(
    input_variables=["messages"],
    template="""
You are a Slack assistant summarizing an internal support or escalation thread. 
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
)
channel_summary_chain = LLMChain(llm=llm, prompt=channel_prompt)

# ─────────────────────────────────────────────────────────────────────────────
# Async Slack helpers
# ─────────────────────────────────────────────────────────────────────────────
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
            name = user_or_bot_id  # last resort (won't happen often)

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
    limit_per_page: int = 200
) -> List[dict]:
    """Fetch all parent messages (exclude replies here)."""
    parents, cursor, page_count, msg_count = [], None, 0, 0
    with timed("fetch_channel_history"):
        while True:
            resp = await _call_with_retry(
                client.conversations_history,
                channel=channel_id,
                limit=limit_per_page,
                cursor=cursor,
                include_all_metadata=True
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
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

    parents.sort(key=lambda m: float(m["ts"]))
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
    logger.info(f"Collected replies: {total_replies} across {len([p for p in parents if int(p.get('reply_count',0) or 0)>0])} parents")
    return replies_map

# ─────────────────────────────────────────────────────────────────────────────
# Public async API
# ─────────────────────────────────────────────────────────────────────────────
async def analyze_entire_channel_async(
    token: str,
    channel_id: str,
    thread_ts: str  # API parity; not used in full-channel mode
) -> str:
    """
    1) Fetch parents & replies (strict order)
    2) For each message/reply, convert <@U…> to @Display Name
    3) Build **minimal JSON** with: thread_id, user_name, text, posted_date, posted_time (+ replies[])
       - posted_date format: 'DD Month 2025' (e.g., '13 August 2025')
       - posted_time format: 'HH:MM IST'
    4) Persist the full JSON file (no skipping)
    5) (Optional) Run LLM if you still want a summary
    """
    total_start = time.perf_counter()
    logger.info(f"Starting analyze_entire_channel(async) | channel_id={channel_id} thread_ts={thread_ts}")

    client = AsyncWebClient(token=token)
    name_cache = UserNameCache()

    # 1) Fetch
    parents = await _fetch_history_paginated(client, channel_id)
    if not parents:
        logger.warning(f"No messages found in <#{channel_id}>.")
        # still persist an empty JSON for traceability
        # await _persist_min_json([], channel_id)
        return f":warning: No messages found in <#{channel_id}>."

    replies_map = await _fetch_all_replies_concurrent(client, channel_id, parents, max_concurrency=12)

    # 2) Resolve speaker names + inline mentions concurrently and build minimal JSON
    with timed("build_minimal_json", extra={
        "parents": len(parents),
        "total_replies": sum(len(v) for v in replies_map.values())
    }):
        # Parent speaker names
        parent_name_tasks = [name_cache.get_name(client, (m.get("user") or m.get("bot_id") or "<unknown>")) for m in parents]
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
                reply_name_tasks.append(name_cache.get_name(client, (r.get("user") or r.get("bot_id") or "<unknown>")))
                reply_text_tasks.append(name_cache.replace_mentions(client, r.get("text", "") or ""))

        reply_names = await asyncio.gather(*reply_name_tasks) if reply_name_tasks else []
        reply_texts = await asyncio.gather(*reply_text_tasks) if reply_text_tasks else []

        # Re-assemble into ordered minimal JSON
        minimal: List[Dict[str, object]] = []
        reply_name_map: Dict[Tuple[str, int], str] = {k: reply_names[i] for i, k in enumerate(reply_keys)} if reply_names else {}
        reply_text_map: Dict[Tuple[str, int], str] = {k: reply_texts[i] for i, k in enumerate(reply_keys)} if reply_texts else {}

        for p_idx, m in enumerate(parents):
            parent_ts = m["ts"]
            posted_date, posted_time = _format_date_time_from_ts(parent_ts)

            rec = {
                "thread_id": parent_ts,
                "user_name": parent_names[p_idx],
                "text": parent_texts[p_idx],
                "posted_date": posted_date,  # 'DD Month 2025' in IST
                "posted_time": posted_time,  # 'HH:MM IST'
            }

            rs: List[Dict[str, str]] = []
            for r_idx, r in enumerate(replies_map.get(parent_ts, [])):
                # Use each reply's own ts for its posted_date/time
                r_ts = r.get("ts") or parent_ts
                r_date, r_time = _format_date_time_from_ts(r_ts)
                rs.append({
                    "thread_id": parent_ts,  # the parent thread id
                    "user_name": reply_name_map.get((parent_ts, r_idx)) or (r.get("user") or r.get("bot_id") or "<unknown>"),
                    "text": reply_text_map.get((parent_ts, r_idx)) or (r.get("text", "") or ""),
                    "posted_date": r_date,
                    "posted_time": r_time,
                })
            if rs:
                rec["replies"] = rs
            minimal.append(rec)

    # 3) Persist compact JSON file (entire file, nothing skipped)
    # json_path = await _persist_min_json(minimal, channel_id)
    # logger.info(f"Minimal JSON persisted at: {json_path}")

    # 4/5) Optional: build a transcript and run LLM (kept for parity; remove if not needed)
    with timed("prepare_llm_transcript"):
        blocks: List[str] = []
        for rec in minimal:
            header = f"*{rec['user_name']}* ({rec['thread_id']}):"
            parts = [f"{header} {rec['text']}"]
            for r in rec.get("replies", []):
                parts.append(f"*{r['user_name']}* ({r['thread_id']}): {r['text']}")
            blocks.append("\n".join(parts))
        raw_all = "\n\n---\n\n".join(blocks)

    with timed("llm_summary"):
        try:
            result = await asyncio.to_thread(channel_summary_chain.run, messages=raw_all)
        except Exception as e:
            logger.error(f"Failed to summarize channel <#{channel_id}>: {e}")
            result = f"❌ Failed to summarize channel <#{channel_id}>: {e}"

    total_elapsed = time.perf_counter() - total_start
    logger.info(
        f"analyze_entire_channel finished | channel_id={channel_id} "
        f"parents={len(parents)} replies={sum(len(v) for v in replies_map.values())} "
        f"total_time={total_elapsed:.3f}s"
    )
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Persistence helper
# ─────────────────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────────────────
# Sync wrapper (if your callers pass a sync WebClient)
# ─────────────────────────────────────────────────────────────────────────────
def analyze_entire_channel(
    client,
    channel_id: str,
    thread_ts: str
) -> str:
    token = getattr(client, "token", None)
    if not token:
        raise ValueError("WebClient missing token")
    return asyncio.run(analyze_entire_channel_async(token, channel_id, thread_ts))
