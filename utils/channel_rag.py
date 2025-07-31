import os
import re
from slack_sdk import WebClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from utils.resolve_user_mentions import resolve_user_mentions
from utils.vector_store import FaissVectorStore
from utils.slack_tools import get_user_name
from utils.thread_store import THREAD_VECTOR_STORES
# one store per Slack‐thread for channel RAG
# THREAD_VECTOR_STORES: dict[str, FaissVectorStore] = {}

# ── LLM + PromptTemplate setup ────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
llm = Ollama(
    model=OLLAMA_MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
    temperature=0,          # low temp → more deterministic
        top_p=0.9,                # nucleus sampling
        top_k=40,                 # restrict to the 40 highest-prob tokens
        repeat_penalty=1.1,   # discourage repeats
        num_predict=2048,       # enough to give a detailed answer
        num_ctx=32768,
)
channel_prompt = PromptTemplate(
    input_variables=["messages"],
    template="""
You are a Slack assistant summarizing an internal support or escalation thread. Below is the full message history with speakers and timestamps:

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
- Format each as:  
  - *@username* decided to … [DD/MM/YYYY HH:MM UTC]

*Action Items*  
- List only explicit follow‑up tasks assigned to someone.  
- Format each as:  
  - *@username* to … [DD/MM/YYYY HH:MM UTC]

*Do not* invent any bullets, sections, or timestamps. If something isn’t in the thread, leave it out—do *not* guess.
"""
)
channel_summary_chain = LLMChain(llm=llm, prompt=channel_prompt)

def analyze_entire_channel(
    client: WebClient,
    channel_id: str,
    thread_ts: str
) -> str:
    # ── fetch every top‐level message + replies ───────────────────────────────
    def safe_number_wrap(text):
        return re.sub(r'(?<!<@U)(\d+%?)(?!>)', r'`\1`', text)
    cursor = None
    blocks = []
    while True:
        resp = client.conversations_history(channel=channel_id, limit=200, cursor=cursor)
        for m in resp["messages"]:
            ts = m["ts"]
            if m.get("thread_ts") and m["thread_ts"] != ts:
                continue
            user_id = m.get("user") or m.get("bot_id", "<unknown>")
            name = get_user_name(client, user_id)
            header = f"*{name}* ({ts}):"
            texts = [f"{header} {m.get('text', '')}"]
            if int(m.get("reply_count", 0)) > 0:
                replies = client.conversations_replies(channel=channel_id, ts=ts, limit=1000)
                for r in replies.get("messages", [])[1:]:
                    r_user = r.get("user") or r.get("bot_id", "<unknown>")
                    r_name = get_user_name(client, r_user)
                    r_ts = r["ts"]
                    texts.append(f"*{r_name}* ({r_ts}): {r.get('text', '')}")
            blocks.append("\n".join(texts))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    if not blocks:
        return f":warning: No messages found in <#{channel_id}>."
    raw_all = resolve_user_mentions(client, "\n\n---\n\n".join(blocks))
    try:
        return channel_summary_chain.run(messages=raw_all)
    except Exception as e:
        return f"❌ Failed to summarize channel <#{channel_id}>: {e}"