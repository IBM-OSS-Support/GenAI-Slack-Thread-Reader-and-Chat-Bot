import os
import re
from slack_sdk import WebClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.vector_store import FaissVectorStore
from chains.chat_chain_mcp import process_message_mcp

# one store per Slack‐thread for channel RAG
THREAD_VECTOR_STORES: dict[str, FaissVectorStore] = {}

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
            # skip replies
            if m.get("thread_ts") and m["thread_ts"] != ts:
                continue

            texts = [m.get("text", "")]
            if int(m.get("reply_count", 0)) > 0:
                replies = client.conversations_replies(channel=channel_id, ts=ts, limit=1000)
                for r in replies.get("messages", [])[1:]:
                    texts.append(r.get("text", ""))
            blocks.append("\n".join(texts))

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    if not blocks:
        return f":warning: No messages found in <#{channel_id}>."

    raw_all = "\n\n---\n\n".join(blocks)
    # wrap numeric tokens to preserve exact values
    raw_all = re.sub(r'(\d+%?)', r'`\1`', raw_all)

    # ── chunk & index into FAISS ────────────────────────────────────────────
    vs = THREAD_VECTOR_STORES.get(thread_ts)
    if not vs:
        idx = f"data/faiss_{thread_ts.replace('.', '_')}.index"
        ds  = f"data/docstore_{thread_ts.replace('.', '_')}.pkl"
        vs  = FaissVectorStore(index_path=idx, docstore_path=ds)
        THREAD_VECTOR_STORES[thread_ts] = vs

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks   = splitter.split_text(raw_all)
    docs     = [ Document(page_content=chunk, metadata={"channel": channel_id}) for chunk in chunks ]
    vs.add_documents(docs)

    # ── generate the channel summary ────────────────────────────────────────
    prompt = f"""
You are a Slack assistant. You are given a Slack thread or channel log in `{raw_all}` format, including user messages with timestamps.

Your job is to summarize the discussion with *zero assumptions*. Follow these exact rules:
Your entire response should be below 3000 chars and it should keep the alignment and spacing.
**If the discussion lacks substance** (e.g. messages are too short, unrelated, non-technical, vague, or just status pings), then say clearly:
**If there are meaningful discussions**, produce **only** the following five sections in Slack markdown:
1. *Summary*  
- One brief sentence summarizing the entire thread.

2. *Business Impact*  
- Explain Revenue at risk (if any).  
- Explain Operational impact (if any).  
- Explain Customer impact (if any).  
- Explain Team impact (if any).  
- Explain Other impacts (if any).

*(Only include bullets for impacts explicitly stated in the thread.)*

3. *Key Points Discussed*  
- 3-5 concise bullets capturing the main discussion points.

4. *Decisions Made*  
- Bullets prefixed with who made the decision, e.g. `@username: decision`.

5. *Action Items*  
- Bullets prefixed with `@username:`, include due-dates if mentioned.

**NEVER infer or imagine content** that isn’t explicitly stated in `{raw_all}`. Do not convert vague hints into conclusions. Do not create example structures.

 **Copy all numeric values (dates, times, percentages, counts) exactly** as in `{raw_all}`. Do not paraphrase them.

 **Do not add explanation, context, suggestions, or markdown outside of the five sections.**

"""
    return process_message_mcp(prompt, thread_ts)
