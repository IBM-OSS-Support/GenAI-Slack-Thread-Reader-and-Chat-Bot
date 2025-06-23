import os
import logging
from slack_sdk import WebClient
from utils.slack_tools import get_user_name
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain.prompts import PromptTemplate
from langchain.chains.summarize import load_summarize_chain
from langchain_community.llms import Ollama
from utils.vector_store import FaissVectorStore
from utils.thread_store import THREAD_VECTOR_STORES

# Initialize LLM (still using the old Ollama import)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

# Prompts for map & reduce steps
map_prompt = PromptTemplate(
    input_variables=["text"],
    template="""
Summarize the following Slack conversation chunk in 1-2 concise sentences:

{text}
"""
)

reduce_prompt = PromptTemplate(
    input_variables=["summaries"],
    template="""
You are a Slack assistant. Here are summaries of conversation chunks:

{summaries}

Produce exactly these five sections in Slack markdown, and only these—stop after Action Items.

*Summary*
- One brief sentence summarizing the entire thread.

*Business Impact*
- Bullets for each impact explicitly stated in the conversation.

*Key Points Discussed*
- 3-5 concise bullets capturing main discussion points.

*Decisions Made*
- Bullets prefixed with who made the decision, e.g. `@username: decision`.

*Action Items*
- Bullets prefixed with `@username:`, include due-dates if mentioned.
"""
)

# Build the new map-reduce summarization chain
summarizer = load_summarize_chain(
    llm,
    chain_type="map_reduce",
    map_prompt=map_prompt,
    combine_prompt=reduce_prompt,
    combine_document_variable_name="summaries",  # ensure the reduce prompt sees {summaries}
    return_intermediate_steps=False,
)

# Text splitter
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

def analyze_entire_channel(
    client: WebClient,
    channel_id: str,
    thread_ts: str
) -> str:
    """
    Fetch channel history, split into chunks, run a MapReduce summarization,
    store chunk summaries in FaissVectorStore, and return the final summary.
    """
    # 1) Retrieve top-level messages + replies
    cursor = None
    blocks = []
    while True:
        resp = client.conversations_history(channel=channel_id, limit=200, cursor=cursor)
        for m in resp.get("messages", []):
            ts = m.get("ts")
            # skip replies here; we'll inline them below
            if m.get("thread_ts") and m.get("thread_ts") != ts:
                continue
            user = m.get("user") or m.get("bot_id", "<unknown>")
            name = get_user_name(client, user)
            text = m.get("text", "")
            if int(m.get("reply_count", 0)) > 0:
                replies = client.conversations_replies(channel=channel_id, ts=ts, limit=1000)
                for r in replies.get("messages", [])[1:]:
                    r_user = r.get("user") or r.get("bot_id", "<unknown>")
                    r_name = get_user_name(client, r_user)
                    r_ts = r.get("ts")
                    text += f"\n*{r_name}* ({r_ts}): {r.get('text', '')}"
            blocks.append(f"*{name}* ({ts}): {text}")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    if not blocks:
        return f":warning: No messages found in <#{channel_id}>."

    # 2) Chunk into Documents
    raw_all = "\n\n---\n\n".join(blocks)
    docs = [
        Document(page_content=chunk, metadata={"channel": channel_id})
        for chunk in text_splitter.split_text(raw_all)
    ]

    # 3) Prepare Faiss store for this thread
    vs = THREAD_VECTOR_STORES.get(thread_ts)
    if not vs:
        idx_path = f"data/faiss_{thread_ts.replace('.', '_')}.index"
        ds_path = f"data/docstore_{thread_ts.replace('.', '_')}.pkl"
        vs = FaissVectorStore(index_path=idx_path, docstore_path=ds_path)
        THREAD_VECTOR_STORES[thread_ts] = vs

    # 4) Summarize via the new chain
    result = summarizer.run(docs)

    # 5) Index summaries for future RAG
    summary_chunks = [s.strip() for s in result.split("\n\n") if s.strip()]
    summary_docs = [
        Document(page_content=text, metadata={"chunk_index": i})
        for i, text in enumerate(summary_chunks)
    ]
    vs.add_documents(summary_docs)

    return result
