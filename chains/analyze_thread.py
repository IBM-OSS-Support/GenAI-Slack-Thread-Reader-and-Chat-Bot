import logging
import time
import random
import os
from datetime import datetime
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from utils.slack_tools import fetch_slack_thread, get_user_name

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
You are a Slack assistant. Here’s a thread (with speakers + timestamps):

{messages}

Produce **exactly** these five sections, using Slack markdown *only*:

*Summary:*  
• One sentence.  

*Business Impact:*  
• Bullet on revenue at risk.  
• Bullet on operational impact.
• Bullet on customer impact if there is any.
• Bullet on team impact if there is any.
• Bullet on other impact if there is any.
• Bullet on other impact if there is any.  

*Key Points Discussed:*  
• 3–5 concise bullets.

*Decisions Made:*  
• Bullets prefixed with who.

*Action Items:*  
• Bullets prefixed with @username and include due-dates if mentioned.

**Don't** add extra headings or paragraphs—stop after the “Action Items” bullets.
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

def analyze_slack_thread(channel_id: str, thread_ts: str, instructions: str = None) -> str:
    # 1) Fetch raw Slack thread messages
    msgs = fetch_slack_thread(channel_id, thread_ts)

    # 2) Build a "speaker: text" blob, sorted by timestamp
    lines = []
    for m in sorted(msgs, key=lambda x: float(x.get("ts", 0))):
        ts_float = float(m.get("ts", 0))
        ts_human = datetime.fromtimestamp(ts_float).strftime("%Y-%m-%d %H:%M:%S")
        user_id  = m.get("user") or m.get("bot_id") or "system"
        # resolve to real username if possible
        try:
            speaker = f"@{get_user_name(user_id)}"
        except Exception:
            speaker = user_id
        text = m.get("text", "").replace("\n", " ")
        lines.append(f"{ts_human} {speaker}: {text}")

    blob = "\n".join(lines)

    # 3) Choose chain and run
    chain_kwargs = {"messages": blob}
    chain = custom_chain if instructions else summarizer
    if instructions:
        chain_kwargs["instructions"] = instructions

    # 4) Retry once on transient errors
    for attempt in range(2):
        try:
            return chain.run(**chain_kwargs)
        except Exception as e:
            logger.warning(f"Summarization attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(random.uniform(1, 3))
            else:
                logger.exception("Summarization failed after retry")
                raise

    return "❌ Sorry, I encountered an error and couldn't process your request."
