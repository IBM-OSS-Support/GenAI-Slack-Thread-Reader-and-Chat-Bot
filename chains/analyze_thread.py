import logging
import time
import random
import os
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from utils.slack_tools import fetch_slack_thread


logger = logging.getLogger(__name__)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Initialize Granite model via Ollama
llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
)

# Default summary prompt
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

*Key Points Discussed:*  
• 3-5 concise bullets.

*Decisions Made:*  
• Bullets prefixed with who (e.g. “@Mei Matteson: Updated the case…”).

*Action Items:*  
• Bullets prefixed with @username and include due-dates if mentioned.

**Don't** add extra headings or paragraphs—stop after the “Action Items” bullets.

""")

# Custom instructions prompt
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

# Chains
summarizer = LLMChain(llm=llm, prompt=default_prompt)
custom_chain = LLMChain(llm=llm, prompt=custom_prompt)

def analyze_slack_thread(channel_id: str, thread_ts: str, instructions: str = None) -> str:
    # Fetch thread messages
    msgs = fetch_slack_thread(channel_id, thread_ts)

    # Build the conversation blob
    lines = []
    for m in sorted(msgs, key=lambda x: float(x.get("ts", 0))):
        user_id = m.get("user") or m.get("bot_id") or "system"
        speaker = f"<@{user_id}>"
        text = m.get("text", "")
        lines.append(f"{text}")
        # print("HAHSHHSHH" ,f"{speaker}: "Fdfff"" {text}")

    blob = "\n".join(lines)

    # Choose chain based on presence of custom instructions
    chain = custom_chain if instructions else summarizer
    run_kwargs = {"messages": blob}
    if instructions:
        run_kwargs["instructions"] = instructions

    # Retry once on transient errors
    for attempt in range(2):
        try:
            return chain.run(**run_kwargs)
        except Exception as e:
            logger.warning(f"Summarization attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(random.uniform(1, 3))
            else:
                logger.exception("Summarization failed after retry")
                raise
    return "❌ Sorry, I encountered an error and couldn't process your request."