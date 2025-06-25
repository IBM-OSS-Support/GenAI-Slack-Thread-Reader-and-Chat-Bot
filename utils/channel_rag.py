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
llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

channel_prompt = PromptTemplate(
    input_variables=["messages"],
    template="""
You are a Slack assistant summarizing internal support or escalation threads. Below is the full message thread with speakers and timestamps:

{messages}

Your output must contain **exactly these five sections**, using **Slack markdown formatting** (asterisks for bold section titles, no bold in body). Do not add anything outside these sections. Do not add explanations.


*Summary*  
- Write **one clear sentence** summarizing the entire thread. Be specific about what triggered the thread (e.g., escalation, request, incident).

*Business Impact*  
- Only include bullets for impacts **explicitly mentioned in the thread**.  
- Use the following bullet format:
  - *Revenue at risk*: Describe risk to Watson or IBM revenue.
  - *Operational impact*: Describe what is blocked or degraded.
  - *Customer impact*: Describe how the customer is affected, including any leadership mention (e.g., CIO-level).
  - *Team impact*: Mention any IBM team concerns, delays, or credibility issues.
  - *Other impacts*: List any escalation triggers (e.g., Duty Manager contacted, credibility risk, etc.)

*Key Points Discussed*  
- List 3–6 bullets summarizing specific events, facts, or updates.  
- Focus on what was done, requested, stated, or observed.  
- Use speaker names **only** if it clarifies the point.  
- Do not add any information not present in the thread.

*Decisions Made*  
- List **all concrete decisions**, even logistical ones (like scheduling a call or taking ownership).  
- Use this format:
  - *@username* decided to ___ [DD/MM/YYYY HH:MM IST]

*Action Items*  
- List only *clearly stated follow-up actions* assigned to specific people.  
- Use this format:
  - *@username* to ___ [DD/MM/YYYY HH:MM IST]  
- Include due-dates only if explicitly mentioned. Do not guess or infer.

---

Strictly follow the format. Do **not invent** any bullet or timestamp. If something is missing, leave it out—do not assume.

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
    def safe_number_wrap(text):
    # Avoid wrapping digits inside user mentions like <@U08PN8WJRAA>
        return re.sub(r'(?<!<@U)(\d+%?)(?!>)', r'`\1`', text)
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
    raw_all = resolve_user_mentions(client,"\n\n---\n\n".join(blocks))
    # raw_all = re.sub(r"(\d+%?)", r"`\1`", raw_all)

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
