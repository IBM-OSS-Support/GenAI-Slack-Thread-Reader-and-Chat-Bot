import os
import re
from slack_sdk import WebClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from utils.vector_store import FaissVectorStore
from utils.slack_tools import get_user_name

# one store per Slack‐thread for channel RAG
THREAD_VECTOR_STORES: dict[str, FaissVectorStore] = {}

# ── LLM + PromptTemplate setup ────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

channel_prompt = PromptTemplate(
    input_variables=["messages"],
    template="""
You are a Slack assistant. You are given a Slack thread or channel log in `{messages}` format, including user messages with timestamps.

Your job is to summarize the discussion with *zero assumptions*. Follow these exact rules:

**Summary*  
- One brief sentence summarizing the entire thread.

**Business Impact**  
- Explain Revenue at risk (if any).  
- Explain Operational impact (if any).  
- Explain Customer impact (if any).  
- Explain Team impact (if any).  
- Explain Other impacts (if any).

*(Only include bullets for impacts explicitly stated in the thread.)*

**Key Points Discussed**  
- 3-5 concise bullets capturing the main discussion points.

**Decisions Made**  
- Bullets prefixed with who made the decision, e.g. `@username: decision`.

**Action Items**  
- Bullets prefixed with `@username:`, include due-dates if mentioned.

Your entire response should be below 3000 chars and it should keep the alignment and spacing.  
**If the discussion lacks substance** (e.g. messages are too short, unrelated, non-technical, vague, or just status pings), then say clearly:  
**If there are meaningful discussions**, produce **only** the above five sections in Slack markdown.

**NEVER infer or imagine content** that isn’t explicitly stated in `{messages}`. Do not convert vague hints into conclusions. Do not create example structures.

**Copy all numeric values (dates, times, percentages, counts) exactly** as in `{messages}`. Do not paraphrase them.

**Do not add explanation, context, suggestions, or markdown outside of the five sections.**
"""
)

channel_summary_chain = LLMChain(llm=llm, prompt=channel_prompt)

def analyze_entire_channel(
    client: WebClient,
    channel_id: str,
    thread_ts: str
) -> str:
    """
    1) Paginate channel history (top‐level messages & their replies).
    2) Concatenate, chunk, and index _all_ that text into FAISS under THREAD_VECTOR_STORES[thread_ts].
    3) Ask your Granite LLM for a channel summary.
    """
    # ── fetch every top‐level message + replies ───────────────────────────────
    cursor = None
    blocks = []
    while True:
        resp = client.conversations_history(channel=channel_id, limit=200, cursor=cursor)
        for m in resp["messages"]:
            ts = m["ts"]
            # skip reply messages here; we'll fetch them below
            if m.get("thread_ts") and m["thread_ts"] != ts:
                continue

            # prefix each message with the poster's name and timestamp
            user_id = m.get("user") or m.get("bot_id", "<unknown>")
            name = get_user_name(client, user_id)
            header = f"*{name}* ({ts}):"
            texts = [f"{header} {m.get('text', '')}"]

            # include any replies
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

    # combine all blocks and escape percentages/digits
    raw_all = "\n\n---\n\n".join(blocks)
    raw_all = re.sub(r"(\d+%?)", r"`\1`", raw_all)

    # ── chunk & index into FAISS ─────────────────────────────────────────────
    vs = THREAD_VECTOR_STORES.get(thread_ts)
    if not vs:
        idx = f"data/faiss_{thread_ts.replace('.', '_')}.index"
        ds = f"data/docstore_{thread_ts.replace('.', '_')}.pkl"
        vs = FaissVectorStore(index_path=idx, docstore_path=ds)
        THREAD_VECTOR_STORES[thread_ts] = vs

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_text(raw_all)
    docs = [Document(page_content=chunk, metadata={"channel": channel_id}) for chunk in chunks]
    vs.add_documents(docs)

    # ── generate summary via chain ───────────────────────────────────────────
    try:
        return channel_summary_chain.run(messages=raw_all)
    except Exception as e:
        return f"❌ Failed to summarize channel <#{channel_id}>: {e}"
