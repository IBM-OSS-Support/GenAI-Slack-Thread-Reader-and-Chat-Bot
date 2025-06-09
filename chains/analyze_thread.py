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

from chains.chat_chain_mcp import process_message_mcp
from utils.slack_tools import fetch_slack_thread, get_user_name
from slack_sdk import WebClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from utils.vector_store import FaissVectorStore
logger = logging.getLogger(__name__)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

default_prompt = PromptTemplate(
    input_variables=["messages"],
    template="""
You are a Slack assistant. Here’s the full thread (with speakers + timestamps):

{messages}

Produce **exactly** these five sections in Slack markdown, and **only** these—stop after Action Items.

*Summary*  
- One brief sentence summarizing the entire thread.

*Business Impact*  
- Explain Revenue at risk (if any).  
- Explain Operational impact (if any).  
- Explain Customer impact (if any).  
- Explain Team impact (if any).  
- Explain Other impacts (if any).

*(Only include bullets for impacts explicitly stated in the thread.)*

*Key Points Discussed*  
- 3-5 concise bullets capturing the main discussion points.

*Decisions Made*  
- Bullets prefixed with who made the decision, e.g. `@username: decision`.

*Action Items*  
- Bullets prefixed with `@username:`, include due-dates if mentioned.
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

summarizer   = LLMChain(llm=llm, prompt=default_prompt)
custom_chain = LLMChain(llm=llm, prompt=custom_prompt)
def analyze_slack_thread(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    instructions: str = None,
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

    blob = "\n".join(lines)

    # 3) Select chain & kwargs
    if instructions:
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
