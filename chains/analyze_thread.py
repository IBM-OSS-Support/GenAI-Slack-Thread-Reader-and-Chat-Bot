# chains/analyze_thread.py

import logging
import time
import random
import os
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

from utils.resolve_user_mentions import resolve_user_mentions
from chains.chat_chain_mcp import process_message_mcp
from utils.slack_tools import fetch_slack_thread, get_user_name
from slack_sdk import WebClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.vector_store import FaissVectorStore
logger = logging.getLogger(__name__)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
# Initialize the Ollama LLM

llm = Ollama(
    model=OLLAMA_MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

default_prompt = PromptTemplate(
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
  - *@username* decided to ___ [DD/MM/YYYY HH:MM UTC]

*Action Items*  
- List only *clearly stated follow-up actions* assigned to specific people.  
- Use this format:
  - *@username* to ___ [DD/MM/YYYY HH:MM UTC]  
- Include due-dates only if explicitly mentioned. Do not guess or infer.

---

Strictly follow the format. Do **not invent** any bullet or timestamp. If something is missing, leave it out—do not assume.

"""
)

custom_prompt = PromptTemplate(
    input_variables=["messages", "instructions"],
    template="""
You are a helpful assistant. Here is a Slack thread conversation between users:

{messages}

User instructions:
{instructions}

Please follow these instructions exactly and respond in plain text.
"""
)
translation_prompt = PromptTemplate(
    input_variables=["text", "language"],
    template=(
        "Translate the following Slack message (in Slack markdown) into {language},\n"
        "preserving all formatting. Do not add or remove any markdown syntax:\n\n"
        "```{text}```"
    )
)
translation_chain = LLMChain(llm=llm, prompt=translation_prompt)

# Ensure data directory exists
if not os.path.exists("data"):
    os.makedirs("data", exist_ok=True)

summarizer   = LLMChain(llm=llm, prompt=default_prompt)
custom_chain = LLMChain(llm=llm, prompt=custom_prompt)
def analyze_slack_thread(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    instructions: str = None,
    default: bool = True
) -> str:
    """
    Fetch a Slack thread via the provided WebClient, format it,
    then run the appropriate LLMChain (default or custom).
    """
    # 1) Fetch raw messages
    try:
        messages = fetch_slack_thread(client, channel_id, thread_ts)
    except Exception as e:
        raise RuntimeError(f"Error fetching thread: {e}")

    # 2) Build timestamped speaker lines
    lines = []
    for m in sorted(messages, key=lambda x: float(x.get("ts", 0))):
        ts = float(m.get("ts", 0))
        human_ts = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        uid = m.get("user") or m.get("bot_id") or "system"
        try:
            speaker = f"@{get_user_name(client, uid)}"
        except:
            speaker = uid
        text = m.get("text", "").replace("\n", " ")
        lines.append(f"{human_ts} {speaker}: {text}")

    blob = resolve_user_mentions ( client,"\n".join(lines))

    # 3) Select chain & kwargs
    if default:
        chain = custom_chain
        kwargs = {"messages": blob, "instructions": instructions}
    else:
        chain = summarizer
        kwargs = {"messages": blob}

    # 4) Run with a single retry
    for attempt in range(2):
        try:
            return chain.run(**kwargs)
        except Exception as e:
            logger.warning(f"Summarization attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(random.uniform(1, 3))
            else:
                logger.exception("Summarization failed after retry")
                raise

    return "❌ Sorry, I couldn't process your request."
